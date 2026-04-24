from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mathart.animation.comfyui_preset_manager import ComfyUIPresetManager
from mathart.core.artifact_schema import ArtifactFamily, validate_artifact_strict
from mathart.core.builtin_backends import AntiFlickerRenderBackend


PRESET_NAME = "dual_controlnet_ipadapter"


def _renumber_workflow(workflow: dict[str, Any], start: int = 101) -> dict[str, Any]:
    ordered_ids = list(workflow.keys())
    mapping = {old: str(start + idx) for idx, old in enumerate(ordered_ids)}
    rewritten: dict[str, Any] = {}
    for old_id in ordered_ids:
        node = json.loads(json.dumps(workflow[old_id]))
        inputs = node.get("inputs", {})
        for key, value in list(inputs.items()):
            if isinstance(value, list) and len(value) == 2 and value[0] in mapping:
                inputs[key] = [mapping[value[0]], value[1]]
        rewritten[mapping[old_id]] = node
    return rewritten


def _find_by_title(payload: dict[str, Any], title: str) -> dict[str, Any]:
    for node in payload["prompt"].values():
        if node.get("_meta", {}).get("title") == title:
            return node
    raise AssertionError(f"Node with title {title!r} not found")


class TestComfyUIPresetManager:
    def test_external_workflow_asset_contains_required_semantic_nodes(self) -> None:
        manager = ComfyUIPresetManager()
        preset_path = manager.resolve_preset_path(PRESET_NAME)
        assert preset_path.exists()
        workflow = manager.load_preset(PRESET_NAME)

        class_types = {node["class_type"] for node in workflow.values()}
        assert "CheckpointLoaderSimple" in class_types
        assert "IPAdapterApply" in class_types
        assert "ControlNetApply" in class_types
        assert "KSampler" in class_types
        assert "VAEEncode" in class_types
        assert "VAEDecode" in class_types

    def test_semantic_injection_survives_node_id_renumbering(self, tmp_path: Path) -> None:
        manager = ComfyUIPresetManager()
        workflow = manager.load_preset(PRESET_NAME)
        renumbered = _renumber_workflow(workflow)
        preset_root = tmp_path / "presets"
        preset_root.mkdir(parents=True, exist_ok=True)
        (preset_root / f"{PRESET_NAME}.json").write_text(
            json.dumps(renumbered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        source = tmp_path / "source.png"
        normal = tmp_path / "normal.png"
        depth = tmp_path / "depth.png"
        identity = tmp_path / "identity.png"
        for path in (source, normal, depth, identity):
            path.write_bytes(b"stub")

        payload = ComfyUIPresetManager(preset_root).assemble_payload(
            preset_name=PRESET_NAME,
            source_image_path=source,
            normal_map_path=normal,
            depth_map_path=depth,
            identity_reference_path=identity,
            prompt="pixel art hero",
            negative_prompt="bad anatomy",
            use_ip_adapter=True,
            ip_adapter_weight=0.91,
            model_checkpoint="custom_model.safetensors",
            normal_weight=1.15,
            depth_weight=0.87,
            steps=32,
            cfg_scale=8.25,
            denoising_strength=0.58,
            seed=123,
            filename_prefix="hero_af",
        )

        assert payload["mathart_lock_manifest"]["workflow_contract"]["injection_mode"] == "semantic_selector"
        assert payload["mathart_lock_manifest"]["preset_name"] == PRESET_NAME
        assert payload["mathart_lock_manifest"]["identity_lock_active"] is True

        assert _find_by_title(payload, "Source Image")["inputs"]["image"] == str(source.resolve())
        assert _find_by_title(payload, "Normal Guide")["inputs"]["image"] == str(normal.resolve())
        assert _find_by_title(payload, "Depth Guide")["inputs"]["image"] == str(depth.resolve())
        assert _find_by_title(payload, "Identity Reference")["inputs"]["image"] == str(identity.resolve())
        assert _find_by_title(payload, "Positive Prompt")["inputs"]["text"] == "pixel art hero"
        assert _find_by_title(payload, "Negative Prompt")["inputs"]["text"] == "bad anatomy"
        assert _find_by_title(payload, "Load Checkpoint")["inputs"]["ckpt_name"] == "custom_model.safetensors"
        assert _find_by_title(payload, "Apply Normal ControlNet")["inputs"]["strength"] == pytest.approx(1.15)
        assert _find_by_title(payload, "Apply Depth ControlNet")["inputs"]["strength"] == pytest.approx(0.87)
        assert _find_by_title(payload, "Apply IP-Adapter")["inputs"]["weight"] == pytest.approx(0.91)
        assert _find_by_title(payload, "KSampler")["inputs"]["seed"] == 123
        assert _find_by_title(payload, "KSampler")["inputs"]["steps"] == 32
        # SESSION-175 P0-LATENT-HEALING: AnimateDiff CFG Burn Prevention.
        # Even when callers pass a legacy 8.25, the preset manager now
        # hard-caps KSampler CFG at 4.5 to prevent SparseCtrl burn-in.
        assert _find_by_title(payload, "KSampler")["inputs"]["cfg"] == pytest.approx(4.5)
        assert _find_by_title(payload, "KSampler")["inputs"]["denoise"] == pytest.approx(0.58)
        assert _find_by_title(payload, "Save Output")["inputs"]["filename_prefix"] == "hero_af"


class TestAntiFlickerPresetAssembly:
    def test_backend_emits_typed_anti_flicker_report_without_live_http(self, tmp_path: Path) -> None:
        backend = AntiFlickerRenderBackend()
        context = {
            "output_dir": str(tmp_path / "anti_flicker"),
            "name": "p1_ai_2d",
            "temporal": {"frame_count": 4, "fps": 12},
            "width": 32,
            "height": 32,
            "preset": {"name": PRESET_NAME},
            "identity_lock": {"enabled": True, "weight": 0.88},
            "comfyui": {
                "style_prompt": "dead cells inspired pixel art",
                "negative_prompt": "flicker, blur",
                "controlnet_normal_weight": 1.0,
                "controlnet_depth_weight": 0.95,
                "steps": 12,
                "cfg_scale": 6.5,
                "denoising_strength": 0.6,
            },
        }

        with patch("requests.post", side_effect=RuntimeError("offline-ci")), patch(
            "requests.get", side_effect=RuntimeError("offline-ci")
        ):
            manifest = backend.execute(context)

        assert manifest.artifact_family == ArtifactFamily.ANTI_FLICKER_REPORT.value
        assert manifest.backend_type == "anti_flicker_render"
        assert set(["workflow_payload", "preset_asset", "temporal_report"]).issubset(manifest.outputs)
        assert Path(manifest.outputs["workflow_payload"]).exists()
        assert Path(manifest.outputs["preset_asset"]).exists()
        assert Path(manifest.outputs["temporal_report"]).exists()

        payload_json = json.loads(Path(manifest.outputs["workflow_payload"]).read_text(encoding="utf-8"))
        assert payload_json["mathart_lock_manifest"]["preset_name"] == PRESET_NAME
        assert payload_json["mathart_lock_manifest"]["workflow_contract"]["selector_fields"] == [
            "class_type",
            "_meta.title",
        ]
        assert _find_by_title(payload_json, "Source Image")["inputs"]["image"].startswith("/")
        assert _find_by_title(payload_json, "Normal Guide")["inputs"]["image"].startswith("/")
        assert _find_by_title(payload_json, "Depth Guide")["inputs"]["image"].startswith("/")
        assert _find_by_title(payload_json, "Apply IP-Adapter")["inputs"]["weight"] == pytest.approx(0.88)

        report_json = json.loads(Path(manifest.outputs["temporal_report"]).read_text(encoding="utf-8"))
        assert report_json["preset_name"] == PRESET_NAME
        assert report_json["workflow_payload_path"] == manifest.outputs["workflow_payload"]
        assert report_json["preset_asset_path"] == manifest.outputs["preset_asset"]

        assert manifest.metadata["preset_name"] == PRESET_NAME
        assert manifest.metadata["frame_count"] == 4
        assert manifest.metadata["fps"] == 12
        assert manifest.metadata["keyframe_count"] >= 1
        assert "frame_sequence" in manifest.metadata["payload"]
        assert "workflow_payload_path" in manifest.metadata["payload"]

        errors = validate_artifact_strict(manifest, min_schema_version="1.0.0")
        assert errors == []
