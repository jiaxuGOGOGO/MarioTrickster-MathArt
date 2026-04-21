from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
from PIL import Image

from mathart.core.artifact_schema import ArtifactFamily, validate_artifact_strict


REPO_ROOT = Path(__file__).resolve().parents[1]
ANIMATION_ROOT = REPO_ROOT / "mathart" / "animation"


def _ensure_animation_namespace() -> None:
    if "mathart.animation" in sys.modules:
        return
    pkg = types.ModuleType("mathart.animation")
    pkg.__path__ = [str(ANIMATION_ROOT)]
    sys.modules["mathart.animation"] = pkg


def _load_animation_module(module_stem: str):
    _ensure_animation_namespace()
    module_name = f"mathart.animation.{module_stem}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    module_path = ANIMATION_ROOT / f"{module_stem}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


controlnet_bridge = _load_animation_module("controlnet_bridge_exporters")
frame_sequence = _load_animation_module("frame_sequence_exporter")
comfyui_preset_manager = _load_animation_module("comfyui_preset_manager")


def test_encode_normal_rgb_maps_z_axis_basis_vector_exactly() -> None:
    rgb = controlnet_bridge.encode_controlnet_normal_rgb(
        np.array([[[0.0, 0.0, 1.0]]], dtype=np.float64)
    )
    assert rgb.shape == (1, 1, 3)
    assert rgb[0, 0].tolist() == [127, 127, 255]


def test_normal_map_exporter_pads_to_8_and_validates_manifest(tmp_path: Path) -> None:
    normals = np.dstack([
        np.zeros((10, 14), dtype=np.float64),
        np.zeros((10, 14), dtype=np.float64),
        np.ones((10, 14), dtype=np.float64),
    ])
    exporter = controlnet_bridge.NormalMapExporter()
    result = exporter.export(normals, output_dir=tmp_path, stem="hero")

    assert result.padding.padded_width == 16
    assert result.padding.padded_height == 16
    assert result.manifest.artifact_family == ArtifactFamily.SPRITE_SINGLE.value
    assert validate_artifact_strict(result.manifest) == []

    image = np.asarray(Image.open(result.image_path))
    assert image.shape == (16, 16, 3)
    assert image[0, 0].tolist() == [127, 127, 255]


def test_depth_map_exporter_supports_polarity_switch(tmp_path: Path) -> None:
    depth = np.array([[0.0, 1.0]], dtype=np.float64)

    near_white_exporter = controlnet_bridge.DepthMapExporter(
        controlnet_bridge.DepthMapExportConfig(invert_polarity=False)
    )
    near_black_exporter = controlnet_bridge.DepthMapExporter(
        controlnet_bridge.DepthMapExportConfig(invert_polarity=True)
    )

    near_white = near_white_exporter.export(depth, output_dir=tmp_path / "white", stem="depth")
    near_black = near_black_exporter.export(depth, output_dir=tmp_path / "black", stem="depth")

    white_img = np.asarray(Image.open(near_white.image_path))
    black_img = np.asarray(Image.open(near_black.image_path))

    assert white_img[0, 0, 0] == 0
    assert white_img[0, 1, 0] == 255
    assert black_img[0, 0, 0] == 255
    assert black_img[0, 1, 0] == 0


def test_frame_sequence_exporter_writes_vhs_contiguous_frames_and_metadata(tmp_path: Path) -> None:
    normals = [
        np.dstack([
            np.zeros((9, 10), dtype=np.float64),
            np.zeros((9, 10), dtype=np.float64),
            np.ones((9, 10), dtype=np.float64),
        ])
        for _ in range(3)
    ]
    exporter = frame_sequence.FrameSequenceExporter(
        frame_sequence.FrameSequenceExportConfig(fps=12)
    )
    result = exporter.export_normal_sequence(
        normals,
        output_dir=tmp_path,
        sequence_name="hero_walk",
    )

    assert result.manifest.artifact_family == ArtifactFamily.IMAGE_SEQUENCE.value
    assert validate_artifact_strict(result.manifest) == []
    assert [path.name for path in result.frame_paths] == [
        "frame_00000.png",
        "frame_00001.png",
        "frame_00002.png",
    ]

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["frame_naming"] == "frame_%05d.png"
    assert metadata["frame_count"] == 3
    assert metadata["fps"] == 12
    assert metadata["frame_width"] == 16
    assert metadata["frame_height"] == 16


def test_dual_controlnet_sequence_payload_injects_only_normal_and_depth(tmp_path: Path) -> None:
    normal_dir = tmp_path / "normal"
    depth_dir = tmp_path / "depth"
    for directory in (normal_dir, depth_dir):
        directory.mkdir(parents=True, exist_ok=True)
        for idx in range(2):
            (directory / f"frame_{idx:05d}.png").write_bytes(b"FAKEPNG")

    manager = comfyui_preset_manager.ComfyUIPresetManager()
    payload = manager.assemble_sequence_payload(
        preset_name=comfyui_preset_manager._NORMAL_DEPTH_SEQUENCE_PRESET_NAME,
        normal_sequence_dir=normal_dir,
        depth_sequence_dir=depth_dir,
        prompt="test dual controlnet",
        frame_count=2,
    )

    lock = payload["mathart_lock_manifest"]
    workflow = payload["prompt"]
    assert lock["preset_name"] == comfyui_preset_manager._NORMAL_DEPTH_SEQUENCE_PRESET_NAME
    assert lock["controlnet_guides"] == ["normal", "depth"]
    assert "rgb" not in lock["sequence_directories"]
    assert lock["workflow_contract"]["preset_family"] == "normal_depth_dual_controlnet"
    assert len(workflow) == 14
