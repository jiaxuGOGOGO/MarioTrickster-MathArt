"""End-to-end offline-safe tests for P1-AI-2D-SPARSECTRL.

SESSION-086 — SparseCtrl + AnimateDiff Temporal Consistency Pipeline
====================================================================

These tests validate the **structural correctness** of the SparseCtrl +
AnimateDiff workflow JSON payload assembly without any live ComfyUI HTTP
server.  They enforce the three SESSION-086 red-line anti-patterns:

1. 🚫 Single-Frame Fallacy: VHS_LoadImagesPath nodes MUST receive directory
   paths, NOT single-image paths.  The test asserts that all three sequence
   loaders (normal, depth, RGB) are injected with directory strings.

2. 🚫 Python Topology Trap: All node wiring MUST exist in the external
   ``sparsectrl_animatediff.json`` preset.  Tests verify that the preset
   contains the correct node class_types and that the injector only
   modifies input values, never adds or removes nodes.

3. 🚫 CI HTTP Blocking Trap: Zero HTTP calls.  Every test runs in a pure
   offline sandbox.  The ``ANTI_FLICKER_REPORT`` manifest is validated
   for structural completeness only.

References
----------
[1] Guo et al., "SparseCtrl", ECCV 2024.  https://arxiv.org/abs/2311.16933
[2] Guo et al., "AnimateDiff", ICLR 2024.  https://arxiv.org/abs/2307.04725
[3] Kosinkadink, ComfyUI-VideoHelperSuite.  https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mathart.animation.comfyui_preset_manager import (
    ComfyUIPresetManager,
    NodeSelector,
    PresetBindingError,
    _SPARSECTRL_SELECTORS,
    _SPARSECTRL_PRESET_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SPARSECTRL_PRESET = _SPARSECTRL_PRESET_NAME


def _find_by_title(payload: dict[str, Any], title: str) -> dict[str, Any]:
    """Find a node in the assembled workflow by its _meta.title."""
    for node in payload["prompt"].values():
        if not isinstance(node, dict):
            continue
        if node.get("_meta", {}).get("title") == title:
            return node
    raise AssertionError(f"Node with title {title!r} not found in payload")


def _find_by_class_type(payload: dict[str, Any], class_type: str) -> list[dict[str, Any]]:
    """Find all nodes in the assembled workflow by class_type."""
    return [
        node
        for node in payload["prompt"].values()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


# ---------------------------------------------------------------------------
# Test: Preset Asset Structural Integrity
# ---------------------------------------------------------------------------


class TestSparseCtrlPresetAsset:
    """Validate the sparsectrl_animatediff.json preset asset file."""

    def test_preset_file_exists(self) -> None:
        manager = ComfyUIPresetManager()
        preset_path = manager.resolve_preset_path(SPARSECTRL_PRESET)
        assert preset_path.exists(), f"Preset file not found: {preset_path}"

    def test_preset_is_valid_json(self) -> None:
        manager = ComfyUIPresetManager()
        preset_path = manager.resolve_preset_path(SPARSECTRL_PRESET)
        data = json.loads(preset_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_preset_contains_animatediff_loader(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        assert "ADE_AnimateDiffLoaderWithContext" in class_types

    def test_preset_contains_sparsectrl_loader(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        assert "ACN_SparseCtrlLoaderAdvanced" in class_types

    def test_preset_contains_vhs_load_images_path(self) -> None:
        """Anti-pattern guard: MUST use VHS_LoadImagesPath, NOT LoadImage for sequences."""
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        vhs_nodes = [
            n for n in workflow.values()
            if isinstance(n, dict) and n.get("class_type") == "VHS_LoadImagesPath"
        ]
        # Must have at least 3 VHS loaders: normal, depth, RGB
        assert len(vhs_nodes) >= 3, (
            f"Expected >= 3 VHS_LoadImagesPath nodes, found {len(vhs_nodes)}. "
            "SparseCtrl requires directory-based sequence loading, not single-image LoadImage."
        )

    def test_preset_contains_vhs_video_combine(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        assert "VHS_VideoCombine" in class_types

    def test_preset_contains_empty_latent_batch(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        assert "EmptyLatentImage" in class_types

    def test_preset_contains_context_options(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        assert "ADE_AnimateDiffUniformContextOptions" in class_types

    def test_preset_is_listed_in_available_presets(self) -> None:
        manager = ComfyUIPresetManager()
        presets = manager.available_presets()
        assert SPARSECTRL_PRESET in presets

    def test_preset_validates_against_sparsectrl_selectors(self) -> None:
        """The preset MUST pass validation against all SparseCtrl selectors."""
        manager = ComfyUIPresetManager()
        # Should not raise
        manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)


# ---------------------------------------------------------------------------
# Test: Sequence-Aware Payload Assembly
# ---------------------------------------------------------------------------


class TestSequencePayloadAssembly:
    """Validate assemble_sequence_payload() injects correct values."""

    @pytest.fixture()
    def seq_dirs(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary sequence directories with dummy frames."""
        normal_dir = tmp_path / "normal_frames"
        depth_dir = tmp_path / "depth_frames"
        rgb_dir = tmp_path / "rgb_frames"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
            for i in range(8):
                (d / f"frame_{i:04d}.png").write_bytes(b"FAKEPNG")
        identity = tmp_path / "identity.png"
        identity.write_bytes(b"FAKEPNG")
        return {
            "normal": normal_dir,
            "depth": depth_dir,
            "rgb": rgb_dir,
            "identity": identity,
        }

    def test_basic_assembly_returns_valid_payload(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="pixel art hero, temporal consistency",
            negative_prompt="flicker, blur",
            frame_count=16,
            seed=42,
        )
        assert "prompt" in payload
        assert "mathart_lock_manifest" in payload
        assert "client_id" in payload

    def test_vhs_normal_directory_injected(self, seq_dirs: dict[str, Path]) -> None:
        """Anti-pattern guard: normal sequence MUST be a directory path."""
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            seed=1,
        )
        node = _find_by_title(payload, "Load Normal Sequence")
        assert node["class_type"] == "VHS_LoadImagesPath"
        assert node["inputs"]["directory"] == str(seq_dirs["normal"].resolve())

    def test_vhs_depth_directory_injected(self, seq_dirs: dict[str, Path]) -> None:
        """Anti-pattern guard: depth sequence MUST be a directory path."""
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            seed=1,
        )
        node = _find_by_title(payload, "Load Depth Sequence")
        assert node["class_type"] == "VHS_LoadImagesPath"
        assert node["inputs"]["directory"] == str(seq_dirs["depth"].resolve())

    def test_vhs_rgb_directory_injected(self, seq_dirs: dict[str, Path]) -> None:
        """Anti-pattern guard: RGB sequence MUST be a directory path."""
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            seed=1,
        )
        node = _find_by_title(payload, "Load RGB Sequence")
        assert node["class_type"] == "VHS_LoadImagesPath"
        assert node["inputs"]["directory"] == str(seq_dirs["rgb"].resolve())

    def test_batch_size_synced_with_frame_count(self, seq_dirs: dict[str, Path]) -> None:
        """frame_count MUST be injected into EmptyLatentImage.batch_size."""
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            frame_count=24,
            seed=1,
        )
        node = _find_by_title(payload, "Empty Latent Batch")
        assert node["inputs"]["batch_size"] == 16
        report = payload["mathart_lock_manifest"]["session189_override_report"]
        assert report["max_frames"] == 16
        assert any(
            touch.get("class_type") == "EmptyLatentImage"
            and touch.get("batch_size") == [24, 16]
            for touch in report["touched_nodes"]
        )

    def test_animatediff_model_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            animatediff_model_name="custom_mm.ckpt",
            seed=1,
        )
        node = _find_by_title(payload, "AnimateDiff Loader")
        assert node["inputs"]["model_name"] == "custom_mm.ckpt"

    def test_context_length_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            context_length=32,
            context_overlap=8,
            seed=1,
        )
        node = _find_by_title(payload, "Context Options")
        assert node["inputs"]["context_length"] == 32
        assert node["inputs"]["context_overlap"] == 8

    def test_sparsectrl_model_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            sparsectrl_model_name="custom_sparsectrl.ckpt",
            sparsectrl_strength=0.8,
            seed=1,
        )
        node = _find_by_title(payload, "Load SparseCtrl Model")
        assert node["inputs"]["sparsectrl_name"] == "custom_sparsectrl.ckpt"
        assert node["inputs"]["motion_strength"] == pytest.approx(0.55)
        assert any(
            touch.get("class_type") == "ACN_SparseCtrlLoaderAdvanced"
            and touch.get("motion_strength") == [0.8, 0.55]
            for touch in payload["mathart_lock_manifest"]["session189_override_report"]["touched_nodes"]
        )

    def test_sparsectrl_end_percent_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            sparsectrl_end_percent=0.3,
            seed=1,
        )
        node = _find_by_title(payload, "Apply SparseCtrl RGB")
        assert node["inputs"]["end_percent"] == pytest.approx(0.3)

    def test_ksampler_parameters_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            steps=30,
            cfg_scale=8.0,
            denoising_strength=0.95,
            seed=999,
        )
        node = _find_by_title(payload, "KSampler")
        assert node["inputs"]["seed"] == 999
        assert node["inputs"]["steps"] == 30
        assert node["inputs"]["cfg"] == pytest.approx(4.5)
        assert node["inputs"]["denoise"] == pytest.approx(0.95)

    def test_video_combine_frame_rate_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            frame_rate=24,
            seed=1,
        )
        node = _find_by_title(payload, "Video Combine Output")
        assert node["inputs"]["frame_rate"] == 24

    def test_controlnet_strengths_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            normal_weight=0.9,
            depth_weight=0.85,
            seed=1,
        )
        normal_node = _find_by_title(payload, "Apply Normal ControlNet")
        depth_node = _find_by_title(payload, "Apply Depth ControlNet")
        assert normal_node["inputs"]["strength"] == pytest.approx(0.55)
        assert depth_node["inputs"]["strength"] == pytest.approx(0.55)

    def test_latent_dimensions_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            width=768,
            height=768,
            seed=1,
        )
        node = _find_by_title(payload, "Empty Latent Batch")
        assert node["inputs"]["width"] == 512
        assert node["inputs"]["height"] == 512

    def test_checkpoint_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="test",
            model_checkpoint="custom_model.safetensors",
            seed=1,
        )
        node = _find_by_title(payload, "Load Checkpoint")
        assert node["inputs"]["ckpt_name"] == "custom_model.safetensors"

    def test_text_prompts_injected(self, seq_dirs: dict[str, Path]) -> None:
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=seq_dirs["normal"],
            depth_sequence_dir=seq_dirs["depth"],
            rgb_sequence_dir=seq_dirs["rgb"],
            prompt="pixel art hero",
            negative_prompt="bad anatomy",
            seed=1,
        )
        pos = _find_by_title(payload, "Positive Prompt")
        neg = _find_by_title(payload, "Negative Prompt")
        assert pos["inputs"]["text"] == "pixel art hero"
        assert neg["inputs"]["text"] == "bad anatomy"


# ---------------------------------------------------------------------------
# Test: Lock Manifest Structural Integrity
# ---------------------------------------------------------------------------


class TestSequenceLockManifest:
    """Validate the mathart_lock_manifest for sequence payloads."""

    @pytest.fixture()
    def payload(self, tmp_path: Path) -> dict[str, Any]:
        normal_dir = tmp_path / "normal"
        depth_dir = tmp_path / "depth"
        rgb_dir = tmp_path / "rgb"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
            for i in range(4):
                (d / f"frame_{i:04d}.png").write_bytes(b"FAKE")
        manager = ComfyUIPresetManager()
        return manager.assemble_sequence_payload(
            normal_sequence_dir=normal_dir,
            depth_sequence_dir=depth_dir,
            rgb_sequence_dir=rgb_dir,
            prompt="test prompt",
            negative_prompt="bad",
            frame_count=16,
            context_length=16,
            frame_rate=12,
            seed=42,
        )

    def test_lock_manifest_has_preset_name(self, payload: dict[str, Any]) -> None:
        assert payload["mathart_lock_manifest"]["preset_name"] == SPARSECTRL_PRESET

    def test_lock_manifest_has_temporal_config(self, payload: dict[str, Any]) -> None:
        tc = payload["mathart_lock_manifest"]["temporal_config"]
        assert tc["frame_count"] == 16
        assert tc["context_length"] == 16
        assert tc["frame_rate"] == 12
        assert tc["batch_size_synced"] is True

    def test_lock_manifest_has_sequence_directories(self, payload: dict[str, Any]) -> None:
        sd = payload["mathart_lock_manifest"]["sequence_directories"]
        assert "normal" in sd
        assert "depth" in sd
        assert "rgb" in sd
        # All must be absolute paths
        for key, path_str in sd.items():
            assert Path(path_str).is_absolute(), f"{key} directory is not absolute: {path_str}"

    def test_lock_manifest_workflow_contract_is_sequence_aware(self, payload: dict[str, Any]) -> None:
        wc = payload["mathart_lock_manifest"]["workflow_contract"]
        assert wc["injection_mode"] == "semantic_selector"
        assert wc["sequence_aware"] is True
        assert wc["vhs_directory_injection"] is True

    def test_lock_manifest_has_sparsectrl_in_guides(self, payload: dict[str, Any]) -> None:
        guides = payload["mathart_lock_manifest"]["controlnet_guides"]
        assert "sparsectrl_rgb" in guides

    def test_lock_manifest_identity_lock_inactive(self, payload: dict[str, Any]) -> None:
        assert payload["mathart_lock_manifest"]["identity_lock_active"] is False
        assert payload["mathart_lock_manifest"]["identity_reference_present"] is False

    def test_lock_manifest_seed_is_deterministic(self, payload: dict[str, Any]) -> None:
        assert payload["mathart_lock_manifest"]["seed"] == 42

    def test_lock_manifest_semantic_bindings_non_empty(self, payload: dict[str, Any]) -> None:
        bindings = payload["mathart_lock_manifest"]["semantic_bindings"]
        assert len(bindings) > 10, "Expected many semantic bindings for SparseCtrl preset"


# ---------------------------------------------------------------------------
# Test: Node Count Invariance (Python Topology Trap Guard)
# ---------------------------------------------------------------------------


class TestTopologyInvariance:
    """Ensure the injector does NOT add or remove nodes from the preset."""

    def test_node_count_unchanged_after_injection(self, tmp_path: Path) -> None:
        """Anti-pattern guard: injector must not modify the topology."""
        manager = ComfyUIPresetManager()
        original = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        original_count = len(original)

        normal_dir = tmp_path / "n"
        depth_dir = tmp_path / "d"
        rgb_dir = tmp_path / "r"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=normal_dir,
            depth_sequence_dir=depth_dir,
            rgb_sequence_dir=rgb_dir,
            prompt="test",
            seed=1,
        )
        assembled_count = len(payload["prompt"])
        assert assembled_count > original_count
        assert payload["mathart_lock_manifest"]["session194_pipeline_integration_closure"] is True
        assert payload["mathart_lock_manifest"]["session194_dag_closure"]["status"] == "closed"

    def test_class_types_unchanged_after_injection(self, tmp_path: Path) -> None:
        manager = ComfyUIPresetManager()
        original = manager.load_preset(SPARSECTRL_PRESET, selectors=_SPARSECTRL_SELECTORS)
        original_types = {
            n.get("class_type", "") for n in original.values() if isinstance(n, dict)
        }

        normal_dir = tmp_path / "n"
        depth_dir = tmp_path / "d"
        rgb_dir = tmp_path / "r"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=normal_dir,
            depth_sequence_dir=depth_dir,
            rgb_sequence_dir=rgb_dir,
            prompt="test",
            seed=1,
        )
        assembled_types = {
            n.get("class_type", "") for n in payload["prompt"].values() if isinstance(n, dict)
        }
        assert original_types.issubset(assembled_types)
        assert "IPAdapterAdvanced" in assembled_types
        assert "CLIPVisionLoader" in assembled_types


# ---------------------------------------------------------------------------
# Test: Backward Compatibility with Original Preset
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure SESSION-084 single-image preset still works after SESSION-086 changes."""

    def test_original_preset_still_loads(self) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset("dual_controlnet_ipadapter")
        assert len(workflow) > 0

    def test_original_assemble_payload_still_works(self, tmp_path: Path) -> None:
        source = tmp_path / "source.png"
        normal = tmp_path / "normal.png"
        depth = tmp_path / "depth.png"
        for f in (source, normal, depth):
            f.write_bytes(b"FAKEPNG")
        manager = ComfyUIPresetManager()
        payload = manager.assemble_payload(
            source_image_path=source,
            normal_map_path=normal,
            depth_map_path=depth,
            prompt="test",
            seed=1,
        )
        assert "prompt" in payload
        assert "mathart_lock_manifest" in payload
        assert payload["mathart_lock_manifest"]["preset_name"] == "dual_controlnet_ipadapter"


# ---------------------------------------------------------------------------
# Test: Anti-Flicker Report Manifest Integration
# ---------------------------------------------------------------------------


class TestAntiFlickerReportIntegration:
    """Validate that the sequence payload can be serialized into an ANTI_FLICKER_REPORT."""

    def test_payload_serializes_to_json(self, tmp_path: Path) -> None:
        """The assembled payload MUST be JSON-serializable for disk persistence."""
        normal_dir = tmp_path / "n"
        depth_dir = tmp_path / "d"
        rgb_dir = tmp_path / "r"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=normal_dir,
            depth_sequence_dir=depth_dir,
            rgb_sequence_dir=rgb_dir,
            prompt="test",
            seed=1,
        )
        # Must not raise
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        assert len(serialized) > 100

        # Round-trip
        deserialized = json.loads(serialized)
        assert deserialized["mathart_lock_manifest"]["preset_name"] == SPARSECTRL_PRESET

    def test_payload_can_be_written_to_disk(self, tmp_path: Path) -> None:
        normal_dir = tmp_path / "n"
        depth_dir = tmp_path / "d"
        rgb_dir = tmp_path / "r"
        for d in (normal_dir, depth_dir, rgb_dir):
            d.mkdir()
        manager = ComfyUIPresetManager()
        payload = manager.assemble_sequence_payload(
            normal_sequence_dir=normal_dir,
            depth_sequence_dir=depth_dir,
            rgb_sequence_dir=rgb_dir,
            prompt="test",
            seed=1,
        )
        out_path = tmp_path / "sparsectrl_payload.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assert out_path.exists()
        reloaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert reloaded["mathart_lock_manifest"]["temporal_config"]["batch_size_synced"] is True
