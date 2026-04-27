"""SESSION-154 (P0-SESSION-151-POLICY-AS-CODE-GATES): Test Suite.

Comprehensive tests for the Knowledge Enforcer Gate Registry, including:
  - Registry lifecycle (singleton, register, list, reset)
  - PixelArtEnforcer: all 10 rules with boundary and nominal cases
  - ColorHarmonyEnforcer: all 5 rules with OKLab math validation
  - Pipeline integration: enforce_render_params, enforce_genotype
  - Source traceability: every violation references its source doc
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the singleton registry before each test."""
    from mathart.quality.gates.enforcer_registry import KnowledgeEnforcerRegistry
    KnowledgeEnforcerRegistry.reset()
    yield
    KnowledgeEnforcerRegistry.reset()


# ---------------------------------------------------------------------------
# Registry Tests
# ---------------------------------------------------------------------------


class TestEnforcerRegistry:
    """Test the KnowledgeEnforcerRegistry singleton and registration."""

    def test_singleton(self):
        from mathart.quality.gates.enforcer_registry import KnowledgeEnforcerRegistry
        a = KnowledgeEnforcerRegistry.get_instance()
        b = KnowledgeEnforcerRegistry.get_instance()
        assert a is b

    def test_reset(self):
        from mathart.quality.gates.enforcer_registry import KnowledgeEnforcerRegistry
        a = KnowledgeEnforcerRegistry.get_instance()
        KnowledgeEnforcerRegistry.reset()
        b = KnowledgeEnforcerRegistry.get_instance()
        assert a is not b

    def test_auto_load(self):
        from mathart.quality.gates.enforcer_registry import get_enforcer_registry
        registry = get_enforcer_registry()
        names = registry.list_all()
        assert "pixel_art_enforcer" in names
        assert "color_harmony_enforcer" in names

    def test_summary_table(self):
        from mathart.quality.gates.enforcer_registry import get_enforcer_registry
        registry = get_enforcer_registry()
        table = registry.summary_table()
        assert "pixel_art_enforcer" in table
        assert "color_harmony_enforcer" in table
        assert "pixel_art.md" in table
        assert "color_science.md" in table


# ---------------------------------------------------------------------------
# PixelArtEnforcer Tests
# ---------------------------------------------------------------------------


class TestPixelArtEnforcer:
    """Test PixelArtEnforcer with real if/clamp logic."""

    def _get_enforcer(self):
        from mathart.quality.gates.pixel_art_enforcer import PixelArtEnforcer
        return PixelArtEnforcer()

    def test_canvas_size_clamp_too_small(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"canvas_size": 8})
        assert result.params["canvas_size"] == 16
        assert len(result.violations) == 1
        assert result.violations[0].severity.value == "clamped"
        assert result.violations[0].source_doc == "pixel_art.md"

    def test_canvas_size_clamp_too_large(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"canvas_size": 128})
        assert result.params["canvas_size"] == 64
        assert result.violations[0].rule_id == "禁止像素画画布尺寸越界"

    def test_canvas_size_nominal(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"canvas_size": 32})
        assert result.params["canvas_size"] == 32
        assert result.is_clean

    def test_palette_size_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"palette_size": 2})
        assert result.params["palette_size"] == 4
        result2 = enforcer.validate({"palette_size": 64})
        assert result2.params["palette_size"] == 32

    def test_interpolation_bilinear_blocked(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"interpolation": "bilinear"})
        assert result.params["interpolation"] == "nearest"
        assert result.violations[0].rule_id == "禁止像素画双线性插值"

    def test_interpolation_bicubic_blocked(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"filter_mode": "bicubic"})
        assert result.params["filter_mode"] == "nearest"

    def test_interpolation_nearest_passes(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"interpolation": "nearest"})
        assert result.is_clean

    def test_anti_aliasing_forced_off(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"anti_aliasing": True})
        assert result.params["anti_aliasing"] is False
        assert result.violations[0].rule_id == "禁止像素画抗锯齿"

    def test_anti_aliasing_already_off(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"anti_aliasing": False})
        assert result.is_clean

    def test_dither_matrix_size_mismatch(self):
        enforcer = self._get_enforcer()
        # 16px canvas should use 2x2 matrix
        result = enforcer.validate({
            "canvas_size": 16,
            "dither_matrix_size": 4,
        })
        assert result.params["dither_matrix_size"] == 2

    def test_dither_matrix_size_large_canvas(self):
        enforcer = self._get_enforcer()
        # 32px canvas should use 4x4 matrix
        result = enforcer.validate({
            "canvas_size": 32,
            "dither_matrix_size": 2,
        })
        assert result.params["dither_matrix_size"] == 4

    def test_jaggies_tolerance_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"jaggies_tolerance": 5})
        assert result.params["jaggies_tolerance"] == 2

    def test_rotsprite_upscale_forced_8x(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"rotsprite_upscale": 4})
        assert result.params["rotsprite_upscale"] == 8

    def test_outline_colors_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"outline_colors": 5})
        assert result.params["outline_colors"] == 3

    def test_subpixel_frames_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"subpixel_frames": 8})
        assert result.params["subpixel_frames"] == 4

    def test_multiple_violations(self):
        """Test that multiple violations are detected in a single pass."""
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "canvas_size": 256,
            "palette_size": 1,
            "interpolation": "bilinear",
            "anti_aliasing": True,
        })
        assert len(result.violations) == 4
        assert result.params["canvas_size"] == 64
        assert result.params["palette_size"] == 4
        assert result.params["interpolation"] == "nearest"
        assert result.params["anti_aliasing"] is False

    def test_source_traceability(self):
        """Every violation MUST reference pixel_art.md."""
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "canvas_size": 256,
            "interpolation": "bilinear",
        })
        for v in result.violations:
            assert v.source_doc == "pixel_art.md"

    def test_field_alias_resolution(self):
        """Test that field aliases are correctly resolved."""
        enforcer = self._get_enforcer()
        # 'sprite_size' is an alias for 'canvas_size'
        result = enforcer.validate({"sprite_size": 8})
        assert result.params["sprite_size"] == 16

    def test_dither_strength_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"dither_strength": 1.5})
        assert result.params["dither_strength"] == 1.0


# ---------------------------------------------------------------------------
# ColorHarmonyEnforcer Tests
# ---------------------------------------------------------------------------


class TestColorHarmonyEnforcer:
    """Test ColorHarmonyEnforcer with real OKLab math."""

    def _get_enforcer(self):
        from mathart.quality.gates.color_harmony_enforcer import ColorHarmonyEnforcer
        return ColorHarmonyEnforcer()

    def test_warm_cool_contrast_insufficient(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "light_hue": 30.0,
            "shadow_hue": 50.0,  # Only 20° apart — too close
        })
        assert result.has_corrections
        # Shadow hue should be shifted to ~165° away from light
        corrected_shadow = result.params["shadow_hue"]
        assert abs(corrected_shadow - 30.0) > 100  # Significant shift

    def test_warm_cool_contrast_good(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "light_hue": 30.0,
            "shadow_hue": 195.0,  # 165° apart — good
        })
        # Should pass without correction for warm-cool
        warm_cool_violations = [
            v for v in result.violations
            if "冷暖" in v.rule_id
        ]
        assert len(warm_cool_violations) == 0

    def test_fill_light_ratio_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"fill_light_ratio": 0.8})
        assert result.params["fill_light_ratio"] == 0.5

    def test_rim_light_ratio_clamp(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({"rim_light_ratio": 0.6})
        assert result.params["rim_light_ratio"] == 0.4

    def test_palette_size_character_context(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "palette_context": "character",
            "palette_size": 32,
        })
        assert result.params["palette_size"] == 16

    def test_palette_size_theme_context(self):
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "palette_context": "theme",
            "palette_size": 8,
        })
        assert result.params["palette_size"] == 16

    def test_source_traceability(self):
        """Every violation MUST reference color_science.md or color_light.md."""
        enforcer = self._get_enforcer()
        result = enforcer.validate({
            "fill_light_ratio": 0.8,
            "light_hue": 30.0,
            "shadow_hue": 50.0,
        })
        for v in result.violations:
            assert v.source_doc in ("color_science.md", "color_light.md")


# ---------------------------------------------------------------------------
# Dead Color Detection Tests (requires OKLab)
# ---------------------------------------------------------------------------


class TestDeadColorDetection:
    """Test dead color detection with real OKLab math."""

    def _get_enforcer(self):
        from mathart.quality.gates.color_harmony_enforcer import ColorHarmonyEnforcer
        return ColorHarmonyEnforcer()

    def test_dead_colors_detected(self):
        """Colors with low chroma + mid lightness should be flagged."""
        from mathart.oklab.color_space import srgb_to_oklab
        # Create a palette with intentionally "dead" colors
        # Gray colors in mid-range: (128, 128, 128), (120, 120, 120)
        dead_palette = np.array([
            [128, 128, 128],
            [120, 120, 120],
            [130, 130, 130],
            [255, 0, 0],      # Vibrant red — not dead
            [0, 0, 255],      # Vibrant blue — not dead
        ], dtype=np.float64)
        oklab = srgb_to_oklab(dead_palette)

        enforcer = self._get_enforcer()
        result = enforcer.validate({"palette_oklab": oklab.copy()})

        dead_violations = [
            v for v in result.violations if "死亡配色" in v.rule_id
        ]
        # Should detect at least 2 dead colors (the grays)
        assert len(dead_violations) >= 2

    def test_vibrant_palette_passes(self):
        """A vibrant palette should not trigger dead color detection."""
        from mathart.oklab.color_space import srgb_to_oklab
        vibrant_palette = np.array([
            [255, 50, 50],    # Red
            [50, 255, 50],    # Green
            [50, 50, 255],    # Blue
            [255, 255, 50],   # Yellow
            [10, 10, 10],     # Near-black (not dead — L < 0.3)
        ], dtype=np.float64)
        oklab = srgb_to_oklab(vibrant_palette)

        enforcer = self._get_enforcer()
        result = enforcer.validate({"palette_oklab": oklab.copy()})

        dead_violations = [
            v for v in result.violations if "死亡配色" in v.rule_id
        ]
        assert len(dead_violations) == 0


# ---------------------------------------------------------------------------
# Lightness Range Tests
# ---------------------------------------------------------------------------


class TestLightnessRange:
    """Test palette lightness range enforcement."""

    def _get_enforcer(self):
        from mathart.quality.gates.color_harmony_enforcer import ColorHarmonyEnforcer
        return ColorHarmonyEnforcer()

    def test_narrow_lightness_range_corrected(self):
        """A palette with L-range < 0.3 should be stretched."""
        from mathart.oklab.color_space import srgb_to_oklab
        # All similar brightness
        narrow_palette = np.array([
            [120, 120, 120],
            [130, 130, 130],
            [125, 125, 125],
            [128, 128, 128],
        ], dtype=np.float64)
        oklab = srgb_to_oklab(narrow_palette)
        original_range = float(np.max(oklab[:, 0]) - np.min(oklab[:, 0]))

        enforcer = self._get_enforcer()
        result = enforcer.validate({"palette_oklab": oklab.copy()})

        lightness_violations = [
            v for v in result.violations if "明度范围" in v.rule_id
        ]
        assert len(lightness_violations) >= 1

    def test_wide_lightness_range_passes(self):
        """A palette with adequate L-range should pass."""
        from mathart.oklab.color_space import srgb_to_oklab
        wide_palette = np.array([
            [20, 20, 20],     # Very dark
            [128, 128, 128],  # Mid
            [240, 240, 240],  # Very light
        ], dtype=np.float64)
        oklab = srgb_to_oklab(wide_palette)

        enforcer = self._get_enforcer()
        result = enforcer.validate({"palette_oklab": oklab.copy()})

        lightness_violations = [
            v for v in result.violations if "明度范围" in v.rule_id
        ]
        assert len(lightness_violations) == 0


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestEnforcerIntegration:
    """Test the pipeline integration layer."""

    def test_enforce_render_params(self):
        from mathart.quality.gates.enforcer_integration import enforce_render_params
        params = {
            "canvas_size": 256,
            "interpolation": "bilinear",
            "anti_aliasing": True,
        }
        corrected, results = enforce_render_params(params, verbose=False)
        assert corrected["canvas_size"] == 64
        assert corrected["interpolation"] == "nearest"
        assert corrected["anti_aliasing"] is False

    def test_enforce_render_params_verbose(self):
        from mathart.quality.gates.enforcer_integration import enforce_render_params
        output_lines = []
        params = {"canvas_size": 256}
        corrected, results = enforce_render_params(
            params, verbose=True, output_fn=output_lines.append,
        )
        assert any("知识执法网关" in line for line in output_lines)

    def test_enforce_render_params_log_to_file(self):
        from mathart.quality.gates.enforcer_integration import enforce_render_params
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "enforcement.json"
            params = {"canvas_size": 256}
            enforce_render_params(params, log_to_file=log_path)
            assert log_path.exists()
            data = json.loads(log_path.read_text(encoding="utf-8"))
            assert "enforcers" in data
            assert data["session"] == "SESSION-154"

    def test_enforcer_summary_report(self):
        from mathart.quality.gates.enforcer_integration import enforcer_summary_report
        report = enforcer_summary_report()
        assert "Knowledge Enforcer Gate" in report
        assert "PixelArtEnforcer" in report or "pixel_art_enforcer" in report

    def test_run_all_enforcers_chain(self):
        """Test that enforcers are chained — corrections from one are
        visible to the next."""
        from mathart.quality.gates.enforcer_registry import run_all_enforcers
        params = {
            "canvas_size": 256,
            "interpolation": "bilinear",
            "fill_light_ratio": 0.9,
        }
        corrected, results = run_all_enforcers(params)
        assert corrected["canvas_size"] == 64
        assert corrected["interpolation"] == "nearest"
        assert corrected["fill_light_ratio"] == 0.5

    def test_enforce_backend_context(self):
        from mathart.quality.gates.enforcer_integration import enforce_backend_context
        context = {
            "canvas_size": 128,
            "downscale_method": "bilinear",
        }
        corrected = enforce_backend_context(
            context, backend_name="test_backend",
        )
        assert corrected["canvas_size"] == 64
        assert corrected["downscale_method"] == "nearest"


# ---------------------------------------------------------------------------
# UX Output Tests
# ---------------------------------------------------------------------------


class TestUXOutput:
    """Test UX-friendly output formatting."""

    def test_violation_log_line(self):
        from mathart.quality.gates.enforcer_registry import (
            EnforcerViolation, EnforcerSeverity,
        )
        v = EnforcerViolation(
            rule_id="test_rule",
            message="Test message",
            severity=EnforcerSeverity.CLAMPED,
            source_doc="pixel_art.md",
            field_name="canvas_size",
            original_value=256,
            corrected_value=64,
        )
        log = v.log_line()
        assert "CLAMPED" in log
        assert "pixel_art.md" in log
        assert "256" in log
        assert "64" in log

    def test_violation_ux_line(self):
        from mathart.quality.gates.enforcer_registry import (
            EnforcerViolation, EnforcerSeverity,
        )
        v = EnforcerViolation(
            rule_id="test_rule",
            message="Test message",
            severity=EnforcerSeverity.CLAMPED,
            source_doc="pixel_art.md",
            field_name="canvas_size",
            original_value=256,
            corrected_value=64,
        )
        ux = v.ux_line()
        assert "知识网关激活" in ux
        assert "pixel_art.md" in ux

    def test_enforcer_result_summary(self):
        from mathart.quality.gates.enforcer_registry import (
            EnforcerResult, EnforcerViolation, EnforcerSeverity,
        )
        result = EnforcerResult(
            enforcer_name="test",
            params={},
            violations=[
                EnforcerViolation(
                    rule_id="r1", message="m1",
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="test.md",
                ),
                EnforcerViolation(
                    rule_id="r2", message="m2",
                    severity=EnforcerSeverity.REJECTED,
                    source_doc="test.md",
                ),
            ],
        )
        summary = result.summary()
        assert summary["total_violations"] == 2
        assert summary["corrections"] == 1
        assert summary["rejections"] == 1
        assert not summary["clean"]
