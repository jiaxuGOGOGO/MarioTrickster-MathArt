"""
SESSION-190 Test Suite: Modal Decoupling + LookDev + I/O Sanitization
=====================================================================
Covers:
  - detect_dummy_mesh: dummy cylinder detection logic
  - hydrate_prompt: semantic hydration when prompt is empty
  - force_decouple_dummy_mesh_payload: ComfyUI workflow modal decoupling
  - Double-quote crusher: path sanitization
  - LookDev action_filter: single-action filtering
  - Hard anchor constants validation
  - Red-line: no proxy env vars in source
"""
import os
import re
import inspect
import pytest

# ---------------------------------------------------------------------------
# Import SESSION-190 public API
# ---------------------------------------------------------------------------
from mathart.core.anti_flicker_runtime import (
    DECOUPLED_DEPTH_NORMAL_STRENGTH,
    DECOUPLED_RGB_STRENGTH,
    DECOUPLED_DENOISE,
    SEMANTIC_HYDRATION_POSITIVE,
    SEMANTIC_HYDRATION_NEGATIVE,
    detect_dummy_mesh,
    hydrate_prompt,
    force_decouple_dummy_mesh_payload,
)

# Also verify SESSION-189 anchors are still intact
from mathart.core.anti_flicker_runtime import (
    MAX_FRAMES,
    LATENT_EDGE,
    NORMAL_MATTE_RGB,
)


# ===== Hard Anchor Constants =====

class TestHardAnchors:
    """Verify SESSION-190 hard anchor constants are correct."""

    def test_decoupled_depth_normal_strength(self):
        # SESSION-195 alignment: SESSION-193 reverted Depth/Normal strength
        # to 0.45 because OpenPose ControlNet now takes over motion control
        # at strength=1.0. With OpenPose carrying the skeletal signal,
        # Depth/Normal only needs to provide supplementary geometric hints,
        # so the contract is softened to >= 0.40 (the SESSION-193 min floor).
        # This is the Fowler "evolving contract" pattern: when the upstream
        # arbitration red line changes, downstream test assertions MUST
        # evolve in lockstep — never skip, never comment out.
        assert DECOUPLED_DEPTH_NORMAL_STRENGTH >= 0.40
        assert DECOUPLED_DEPTH_NORMAL_STRENGTH <= 1.0

    def test_decoupled_rgb_strength(self):
        assert DECOUPLED_RGB_STRENGTH == 0.0

    def test_decoupled_denoise(self):
        assert DECOUPLED_DENOISE == 1.0

    def test_session189_anchors_preserved(self):
        """SESSION-189 anchors MUST NOT be modified."""
        assert MAX_FRAMES == 16
        assert LATENT_EDGE == 512
        assert NORMAL_MATTE_RGB == (128, 128, 255)


# ===== Semantic Hydration Constants =====

class TestSemanticHydration:
    """Verify semantic hydration prompts are non-empty and well-formed."""

    def test_positive_prompt_non_empty(self):
        assert len(SEMANTIC_HYDRATION_POSITIVE) > 20

    def test_negative_prompt_non_empty(self):
        assert len(SEMANTIC_HYDRATION_NEGATIVE) > 10

    def test_positive_contains_quality_keywords(self):
        lower = SEMANTIC_HYDRATION_POSITIVE.lower()
        assert "masterpiece" in lower or "best quality" in lower

    def test_negative_contains_quality_keywords(self):
        lower = SEMANTIC_HYDRATION_NEGATIVE.lower()
        assert "worst" in lower or "blur" in lower or "low quality" in lower


# ===== detect_dummy_mesh =====

class TestDetectDummyMesh:
    """Test dummy mesh detection logic."""

    def test_positive_detection_pseudo3d_shell(self):
        ctx = {"backend_type": "pseudo_3d_shell"}
        assert detect_dummy_mesh(ctx) is True

    def test_positive_detection_cylinder_params(self):
        ctx = {"cylinder_radius": 0.5, "cylinder_height": 1.0}
        assert detect_dummy_mesh(ctx) is True

    def test_positive_detection_nested_backend(self):
        ctx = {"render_config": {"backend_type": "pseudo_3d_shell"}}
        assert detect_dummy_mesh(ctx) is True

    def test_negative_detection_empty_context(self):
        assert detect_dummy_mesh({}) is False

    def test_negative_detection_real_mesh(self):
        ctx = {"_pseudo3d_shell_active": False, "_dummy_cylinder_mesh": False}
        assert detect_dummy_mesh(ctx) is False

    def test_negative_detection_no_key(self):
        ctx = {"some_other_key": True}
        assert detect_dummy_mesh(ctx) is False


# ===== hydrate_prompt =====

class TestHydratePrompt:
    """Test semantic hydration prompt injection."""

    def test_injects_when_both_empty(self):
        ctx = {"vibe": "", "style_prompt": ""}
        result = hydrate_prompt(ctx)
        assert len(result.get("style_prompt", "")) > 10

    def test_injects_when_short_prompt(self):
        ctx = {"vibe": "hi", "style_prompt": "abc"}
        result = hydrate_prompt(ctx)
        assert len(result.get("style_prompt", "")) > 10

    def test_preserves_existing_long_prompt(self):
        long_prompt = "a very detailed description of a character with many attributes and qualities"
        ctx = {"vibe": "", "style_prompt": long_prompt}
        result = hydrate_prompt(ctx)
        # Should preserve the existing prompt (not overwrite)
        assert long_prompt in result.get("style_prompt", "")

    def test_returns_dict(self):
        ctx = {"vibe": "", "style_prompt": ""}
        result = hydrate_prompt(ctx)
        assert isinstance(result, dict)

    # ── SESSION-208: Chinese vibe detection and translation ──

    def test_chinese_vibe_triggers_hydration(self):
        """SESSION-208: Chinese vibe must trigger hydration regardless of length."""
        ctx = {"vibe": "赛博朋克风格的像素在雨中奔跑", "style_prompt": ""}
        result = hydrate_prompt(ctx)
        assert result["_session190_semantic_hydration"] is True
        # Result must NOT contain Chinese (CLIP can't parse it)
        style = result.get("style_prompt", "")
        assert len(style) > 10

    def test_chinese_vibe_translated_flag(self):
        """SESSION-208: Chinese vibe should set _session208_vibe_translated."""
        ctx = {"vibe": "活泼的跳跃", "style_prompt": ""}
        result = hydrate_prompt(ctx)
        assert result["_session190_semantic_hydration"] is True
        # The flag should exist
        assert "_session208_vibe_translated" in result

    def test_english_long_vibe_preserved(self):
        """SESSION-208: Long English vibe should NOT trigger hydration."""
        long_english = "cyberpunk pixel character running in rain with neon lights"
        ctx = {"vibe": long_english, "style_prompt": ""}
        result = hydrate_prompt(ctx)
        assert result["_session190_semantic_hydration"] is False


# ===== force_decouple_dummy_mesh_payload =====

class TestForceDecoupleDummyMeshPayload:
    """Test ComfyUI workflow modal decoupling."""

    def _make_workflow(self):
        return {
            "1": {
                "class_type": "KSampler",
                "inputs": {"denoise": 0.75, "cfg": 7.0},
            },
            "2": {
                "class_type": "ACN_SparseCtrlRGBPreprocessor",
                "inputs": {"strength": 0.8},
            },
            "3": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.6},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "original prompt"},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512, "batch_size": 16},
            },
        }

    def test_denoise_forced_to_1(self):
        wf = self._make_workflow()
        force_decouple_dummy_mesh_payload(wf)
        assert wf["1"]["inputs"]["denoise"] == DECOUPLED_DENOISE

    def test_rgb_strength_zeroed(self):
        wf = self._make_workflow()
        force_decouple_dummy_mesh_payload(wf)
        assert wf["2"]["inputs"]["strength"] == DECOUPLED_RGB_STRENGTH

    def test_depth_normal_strength_reduced(self):
        wf = self._make_workflow()
        force_decouple_dummy_mesh_payload(wf)
        assert wf["3"]["inputs"]["strength"] == DECOUPLED_DEPTH_NORMAL_STRENGTH

    def test_returns_report_dict(self):
        wf = self._make_workflow()
        report = force_decouple_dummy_mesh_payload(wf)
        assert isinstance(report, dict)

    def test_empty_workflow_no_crash(self):
        """Empty workflow should not crash."""
        report = force_decouple_dummy_mesh_payload({})
        assert isinstance(report, dict)


# ===== Double-Quote Crusher =====

class TestDoubleQuoteCrusher:
    """Test path sanitization logic (double-quote crusher)."""

    def test_strip_double_quotes(self):
        raw = '"C:\\Users\\test\\ref.gif"'
        cleaned = raw.strip('"').strip("'").strip()
        assert cleaned == "C:\\Users\\test\\ref.gif"

    def test_strip_single_quotes(self):
        raw = "'C:\\Users\\test\\ref.gif'"
        cleaned = raw.strip('"').strip("'").strip()
        assert cleaned == "C:\\Users\\test\\ref.gif"

    def test_strip_mixed_whitespace(self):
        raw = '  "C:\\Users\\test\\ref.gif"  '
        cleaned = raw.strip().strip('"').strip("'").strip()
        assert cleaned == "C:\\Users\\test\\ref.gif"

    def test_no_quotes_unchanged(self):
        raw = "C:\\Users\\test\\ref.gif"
        cleaned = raw.strip('"').strip("'").strip()
        assert cleaned == raw


# ===== Red-Line: No Proxy Env Vars =====

class TestRedLineNoProxyEnvVars:
    """Ensure anti_flicker_runtime.py never references proxy env vars."""

    def test_module_source_never_touches_proxy_env(self):
        src_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mathart", "core", "anti_flicker_runtime.py",
        )
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            # Allow in comments but not in actual code
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert forbidden not in stripped, (
                    f"Forbidden env var reference '{forbidden}' found in "
                    f"anti_flicker_runtime.py (non-comment line): {stripped}"
                )


# ===== Red-Line: No Node ID Hardcoding =====

class TestRedLineNoNodeIdHardcoding:
    """Ensure force_decouple_dummy_mesh_payload uses class_type, not node IDs."""

    def test_function_source_no_hardcoded_node_ids(self):
        source = inspect.getsource(force_decouple_dummy_mesh_payload)
        # Should not contain patterns like workflow["123"] or workflow['456']
        hardcoded_pattern = re.compile(r'workflow\s*\[\s*["\']?\d+["\']?\s*\]')
        matches = hardcoded_pattern.findall(source)
        assert len(matches) == 0, (
            f"Hardcoded node ID references found in "
            f"force_decouple_dummy_mesh_payload: {matches}"
        )

    def test_function_uses_class_type(self):
        source = inspect.getsource(force_decouple_dummy_mesh_payload)
        assert "class_type" in source, (
            "force_decouple_dummy_mesh_payload must use class_type for "
            "semantic node matching"
        )
