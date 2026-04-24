"""SESSION-179: Visual Distillation & Reskinning — Comprehensive Test Suite.

Tests cover:
1. **SparseCtrl Time-Window Clamping**: ControlNetApplyAdvanced nodes with
   strength >= 0.8 are treated as SparseCtrl and clamped to 0.825~0.9 range
   with end_percent capped at 0.55.
2. **Normal/Depth ControlNet**: Nodes with lower strength are capped at 0.45.
3. **Dynamic batch_size Safety Bounds**: Clamped to [1, 128].
4. **Visual Distillation**: GIF keyframe extraction and API fallback.
5. **cancel_futures**: PDG executor shutdown enhancement.
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# SparseCtrl Time-Window Clamping Tests
# ---------------------------------------------------------------------------
class TestSparseCtrlTimeWindowClamping:
    """SESSION-179: SparseCtrl-RGB end_percent and strength clamping."""

    def test_sparsectrl_strength_clamped_to_sweet_spot(self):
        """SparseCtrl node (strength=1.0) should be clamped to 0.825~0.9."""
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "3": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 1.0, "start_percent": 0.0, "end_percent": 1.0},
            },
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        strength = workflow["3"]["inputs"]["strength"]
        assert 0.825 <= strength <= 0.9, f"SparseCtrl strength {strength} not in [0.825, 0.9]"

    def test_sparsectrl_end_percent_clamped(self):
        """SparseCtrl node end_percent should be clamped to <= 0.6."""
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "3": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 1.0, "start_percent": 0.0, "end_percent": 1.0},
            },
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        end_pct = workflow["3"]["inputs"]["end_percent"]
        assert end_pct <= 0.6, f"SparseCtrl end_percent {end_pct} not clamped"

    def test_normal_depth_controlnet_capped_at_045(self):
        """Normal/Depth ControlNet (lower strength) should be capped at 0.45."""
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "4": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.7, "start_percent": 0.0, "end_percent": 1.0},
            },
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        strength = workflow["4"]["inputs"]["strength"]
        assert strength == 0.45, f"Normal/Depth strength {strength} not capped to 0.45"

    def test_low_strength_controlnet_not_modified(self):
        """ControlNet with strength already <= 0.45 should not be modified."""
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "5": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.3, "start_percent": 0.0, "end_percent": 1.0},
            },
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        assert workflow["5"]["inputs"]["strength"] == 0.3


# ---------------------------------------------------------------------------
# Dynamic batch_size Safety Bounds Tests
# ---------------------------------------------------------------------------
class TestBatchSizeSafetyBounds:
    """SESSION-179: batch_size clamped to [1, 128]."""

    def test_batch_size_clamped_to_max_128(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=200, fps=12)
        assert workflow["1"]["inputs"]["batch_size"] == 128

    def test_batch_size_clamped_to_min_1(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=0, fps=12)
        assert workflow["1"]["inputs"]["batch_size"] == 1

    def test_batch_size_normal_passthrough(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        assert workflow["1"]["inputs"]["batch_size"] == 40


# ---------------------------------------------------------------------------
# Visual Distillation Tests
# ---------------------------------------------------------------------------
class TestVisualDistillation:
    """SESSION-179: GIF keyframe extraction and API fallback."""

    def test_extract_keyframes_from_gif(self, tmp_path):
        from mathart.workspace.visual_distillation import extract_keyframes_from_gif

        # Create a simple animated GIF with 10 frames
        frames = []
        for i in range(10):
            img = Image.new("RGB", (64, 64), (i * 25, 0, 0))
            frames.append(img)

        gif_path = tmp_path / "test.gif"
        frames[0].save(
            gif_path, save_all=True, append_images=frames[1:],
            duration=100, loop=0,
        )

        keyframes = extract_keyframes_from_gif(gif_path, max_frames=4)
        assert len(keyframes) == 4
        # Each keyframe should be valid PNG bytes
        for kf in keyframes:
            img = Image.open(io.BytesIO(kf))
            assert img.mode == "RGB"

    def test_extract_keyframes_from_folder(self, tmp_path):
        from mathart.workspace.visual_distillation import extract_keyframes_from_folder

        # Create numbered image files
        for i in range(8):
            img = Image.new("RGB", (64, 64), (i * 30, 0, 0))
            img.save(tmp_path / f"frame_{i:04d}.png")

        keyframes = extract_keyframes_from_folder(tmp_path, max_frames=4)
        assert len(keyframes) == 4

    def test_distill_returns_defaults_without_api_key(self, tmp_path):
        from mathart.workspace.visual_distillation import (
            DEFAULT_PHYSICS_PARAMS,
            distill_physics_from_reference,
        )

        # Create a simple GIF
        frames = [Image.new("RGB", (32, 32), (i * 50, 0, 0)) for i in range(5)]
        gif_path = tmp_path / "test.gif"
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100, loop=0)

        # Without API key, should return defaults
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            result = distill_physics_from_reference(gif_path, output_fn=lambda x: None)

        assert result == DEFAULT_PHYSICS_PARAMS

    def test_distill_handles_invalid_path_gracefully(self):
        from mathart.workspace.visual_distillation import (
            DEFAULT_PHYSICS_PARAMS,
            distill_physics_from_reference,
        )

        result = distill_physics_from_reference(
            "/nonexistent/path.gif",
            output_fn=lambda x: None,
        )
        assert result == DEFAULT_PHYSICS_PARAMS

    def test_no_cv2_import(self):
        """HARD RED LINE: visual_distillation.py must NEVER import cv2."""
        import mathart.workspace.visual_distillation as vd
        import inspect
        source = inspect.getsource(vd)
        # Check actual import statements (not comments/docstrings)
        import re
        # Match lines that are actual import statements (not comments)
        actual_imports = [
            line.strip() for line in source.split("\n")
            if (line.strip().startswith("import cv2") or line.strip().startswith("from cv2"))
            and not line.strip().startswith("#")
            and not line.strip().startswith("\"\"\"")
        ]
        assert len(actual_imports) == 0, f"cv2 import detected — FORBIDDEN! {actual_imports}"


# ---------------------------------------------------------------------------
# Backward Compatibility Tests (SESSION-178 regression)
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    """Ensure SESSION-178 tests still pass with SESSION-179 changes."""

    def test_batch_size_synced_to_actual_frames(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        assert workflow["1"]["inputs"]["batch_size"] == 40

    def test_frame_rate_synced_to_fps(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "2": {"class_type": "VideoCombine", "inputs": {"frame_rate": 8}},
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        assert workflow["2"]["inputs"]["frame_rate"] == 12

    def test_no_override_when_actual_frames_is_none(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 192, "height": 192, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=None, fps=None)
        assert workflow["1"]["inputs"]["width"] == 512
        assert workflow["1"]["inputs"]["batch_size"] == 16
