"""Tests for SESSION-178: Full Sync and True Abort.

This module verifies the three core patches landed in SESSION-178:

1. **Dynamic Latent Batch Alignment**: ``_force_latent_canvas_512`` must
   override ``EmptyLatentImage.batch_size`` and ``VideoCombine.frame_rate``
   when ``actual_frames`` and ``fps`` are provided.
2. **Matting**: ``_jit_upscale_image`` must composite RGBA images onto a
   solid background of ``matting_color`` to remove alpha.
3. **Download Loop Poison Pill**: ``ComfyUIClient._download_file`` must
   raise ``ComfyUIExecutionError`` when the URLError contains 10054/10061.
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import urllib.error

import pytest
from PIL import Image


def _make_rgba_fixture(tmp_path: Path) -> Path:
    """Create a 192x192 RGBA PNG with a transparent background and an opaque red square."""
    img = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    for y in range(50, 100):
        for x in range(50, 100):
            img.putpixel((x, y), (255, 0, 0, 255))
    path = tmp_path / "rgba.png"
    img.save(path)
    return path


class TestDynamicLatentBatchAlignment:
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

    def test_controlnet_strength_capped(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "3": {"class_type": "ControlNetApplyAdvanced", "inputs": {"strength": 1.0}},
        }
        _force_latent_canvas_512(workflow, actual_frames=40, fps=12)
        # SESSION-179: SparseCtrl sweet spot is 0.825~0.9
        assert 0.825 <= workflow["3"]["inputs"]["strength"] <= 0.9

    def test_no_override_when_actual_frames_is_none(self):
        from mathart.backend.ai_render_stream_backend import _force_latent_canvas_512

        workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 192, "height": 192, "batch_size": 16}},
        }
        _force_latent_canvas_512(workflow, actual_frames=None, fps=None)
        # Width/height still upgraded to 512
        assert workflow["1"]["inputs"]["width"] == 512
        # batch_size left untouched
        assert workflow["1"]["inputs"]["batch_size"] == 16


class TestMatting:
    def test_normal_matting_fills_purple_blue(self, tmp_path):
        from mathart.backend.ai_render_stream_backend import _jit_upscale_image

        path = _make_rgba_fixture(tmp_path)
        data = _jit_upscale_image(path, is_mask=False, matting_color=(128, 128, 255))
        img = Image.open(io.BytesIO(data))
        assert img.mode == "RGB"
        # Top-left corner (transparent in source) must be matted to purple-blue
        assert img.getpixel((0, 0)) == (128, 128, 255)

    def test_depth_matting_fills_black(self, tmp_path):
        from mathart.backend.ai_render_stream_backend import _jit_upscale_image

        path = _make_rgba_fixture(tmp_path)
        data = _jit_upscale_image(path, is_mask=False, matting_color=(0, 0, 0))
        img = Image.open(io.BytesIO(data))
        assert img.mode == "RGB"
        assert img.getpixel((0, 0)) == (0, 0, 0)

    def test_original_file_not_modified(self, tmp_path):
        from mathart.backend.ai_render_stream_backend import _jit_upscale_image

        path = _make_rgba_fixture(tmp_path)
        original_bytes = path.read_bytes()
        _jit_upscale_image(path, is_mask=False, matting_color=(128, 128, 255))
        assert path.read_bytes() == original_bytes, "JIT must NEVER modify the original file"


class TestDownloadPoisonPill:
    def test_winerror_10054_raises_comfyui_execution_error(self):
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient, ComfyUIExecutionError

        client = ComfyUIClient(server_address="http://localhost:8188")
        err = urllib.error.URLError("[WinError 10054] Connection reset by peer")
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ComfyUIExecutionError):
                client._download_file("test.png", "", "output", output_dir=None)

    def test_winerror_10061_raises_comfyui_execution_error(self):
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient, ComfyUIExecutionError

        client = ComfyUIClient(server_address="http://localhost:8188")
        err = urllib.error.URLError("[WinError 10061] Connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ComfyUIExecutionError):
                client._download_file("test.png", "", "output", output_dir=None)
