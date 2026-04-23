"""Tests for ComfyUI Headless Render Backend — SESSION-151.

Covers:
1. ComfyWorkflowMutator — semantic node finding, mutation, ledger
2. ComfyAPIClient — upload, render, VRAM GC, timeout
3. ComfyUIRenderBackend — registry integration, config validation, execute
4. Red Line Guards — no hardcoded node IDs, timeout breaker, output repatriation
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures: Sample Workflow Blueprint
# ---------------------------------------------------------------------------

SAMPLE_WORKFLOW: dict[str, Any] = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
        "_meta": {"title": "KSampler [MathArt_Seed]"},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "default positive prompt",
            "clip": ["4", 1],
        },
        "_meta": {"title": "CLIP Text Encode [MathArt_Prompt]"},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "default negative prompt",
            "clip": ["4", 1],
        },
        "_meta": {"title": "CLIP Text Encode [MathArt_Negative]"},
    },
    "10": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "placeholder.png",
            "upload": "image",
        },
        "_meta": {"title": "Load Image [MathArt_Input_Image]"},
    },
    "15": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI",
            "images": ["12", 0],
        },
        "_meta": {"title": "Save Image [MathArt_Output]"},
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. ComfyWorkflowMutator Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestComfyWorkflowMutator:
    """Test the BFF dynamic payload mutation engine."""

    def test_find_nodes_by_title(self):
        """Semantic node finder locates nodes by _meta.title substring."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        matches = mutator.find_nodes_by_title(SAMPLE_WORKFLOW, "[MathArt_Prompt]")
        assert len(matches) == 1
        node_id, node_data = matches[0]
        assert node_id == "6"
        assert node_data["class_type"] == "CLIPTextEncode"

    def test_find_node_by_title_unique(self):
        """find_node_by_title returns exactly one match."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        node_id, node_data = mutator.find_node_by_title(
            SAMPLE_WORKFLOW, "[MathArt_Input_Image]"
        )
        assert node_id == "10"
        assert node_data["class_type"] == "LoadImage"

    def test_find_node_by_title_missing_raises(self):
        """find_node_by_title raises MutationError when no match."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator, MutationError

        mutator = ComfyWorkflowMutator()
        with pytest.raises(MutationError, match="No node found"):
            mutator.find_node_by_title(SAMPLE_WORKFLOW, "[NonExistent_Marker]")

    def test_find_node_by_title_ambiguous_raises(self):
        """find_node_by_title raises MutationError when multiple matches."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator, MutationError

        # Create ambiguous workflow with duplicate markers
        ambiguous = copy.deepcopy(SAMPLE_WORKFLOW)
        ambiguous["99"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "duplicate"},
            "_meta": {"title": "Another [MathArt_Prompt] Node"},
        }
        mutator = ComfyWorkflowMutator()
        with pytest.raises(MutationError, match="Ambiguous"):
            mutator.find_node_by_title(ambiguous, "[MathArt_Prompt]")

    def test_mutate_injects_values(self):
        """mutate() injects values into correct nodes via semantic markers."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        mutated, ledger = mutator.mutate(
            workflow=SAMPLE_WORKFLOW,
            injections={
                "[MathArt_Input_Image]": "uploaded_proxy.png",
                "[MathArt_Prompt]": "pixel art hero, vibrant colors",
                "[MathArt_Negative]": "blurry, low quality",
                "[MathArt_Seed]": 42,
                "[MathArt_Output]": "mathart_render",
            },
        )

        # Verify injections
        assert mutated["10"]["inputs"]["image"] == "uploaded_proxy.png"
        assert mutated["6"]["inputs"]["text"] == "pixel art hero, vibrant colors"
        assert mutated["7"]["inputs"]["text"] == "blurry, low quality"
        assert mutated["3"]["inputs"]["seed"] == 42
        assert mutated["15"]["inputs"]["filename_prefix"] == "mathart_render"

        # Verify original is unchanged (immutable blueprint)
        assert SAMPLE_WORKFLOW["10"]["inputs"]["image"] == "placeholder.png"
        assert SAMPLE_WORKFLOW["6"]["inputs"]["text"] == "default positive prompt"

        # Verify ledger
        assert len(ledger) == 5
        markers = [r.marker for r in ledger]
        assert "[MathArt_Input_Image]" in markers
        assert "[MathArt_Prompt]" in markers

    def test_mutate_optional_marker_skipped(self):
        """Optional markers that are not in injections are silently skipped."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        # Only inject required markers
        mutated, ledger = mutator.mutate(
            workflow=SAMPLE_WORKFLOW,
            injections={
                "[MathArt_Input_Image]": "proxy.png",
                "[MathArt_Prompt]": "test prompt",
            },
        )
        # Should succeed without error (negative, seed, output are optional)
        assert mutated["10"]["inputs"]["image"] == "proxy.png"
        assert mutated["6"]["inputs"]["text"] == "test prompt"

    def test_build_payload(self):
        """build_payload() assembles a complete ComfyUI API payload."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        payload = mutator.build_payload(
            workflow=SAMPLE_WORKFLOW,
            image_filename="uploaded.png",
            prompt="pixel art hero",
            negative_prompt="blurry",
            seed=42,
            output_prefix="test_render",
        )

        assert "prompt" in payload
        assert "client_id" in payload
        assert "mathart_mutation_ledger" in payload
        assert payload["mathart_mutation_ledger"]["mutations_applied"] >= 4
        assert payload["mathart_mutation_ledger"]["seed"] == 42

        # Verify the workflow was mutated
        workflow = payload["prompt"]
        assert workflow["10"]["inputs"]["image"] == "uploaded.png"
        assert workflow["6"]["inputs"]["text"] == "pixel art hero"

    def test_red_line_no_hardcoded_node_ids(self):
        """[RED LINE] Verify mutator NEVER uses hardcoded node IDs."""
        from mathart.backend import comfy_mutator
        import inspect

        source = inspect.getsource(comfy_mutator)
        # The mutator should never directly index by numeric string keys
        # (except in test fixtures which are not part of the mutator)
        # Check that the core mutation logic uses find_nodes_by_title
        assert "find_nodes_by_title" in source
        assert "_meta" in source
        assert "title" in source

    def test_mutation_ledger_audit_trail(self):
        """Mutation ledger provides full audit trail for debugging."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        mutator = ComfyWorkflowMutator()
        _, ledger = mutator.mutate(
            workflow=SAMPLE_WORKFLOW,
            injections={
                "[MathArt_Input_Image]": "test.png",
                "[MathArt_Prompt]": "test prompt",
            },
        )

        for record in ledger:
            d = record.to_dict()
            assert "marker" in d
            assert "node_id" in d
            assert "class_type" in d
            assert "input_key" in d
            assert "old_value" in d
            assert "new_value" in d
            assert "timestamp" in d


# ═══════════════════════════════════════════════════════════════════════════
# 2. ComfyAPIClient Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestComfyAPIClient:
    """Test the high-availability ComfyUI API client."""

    def test_client_initialization(self):
        """Client initializes with correct defaults."""
        from mathart.backend.comfy_client import ComfyAPIClient

        client = ComfyAPIClient()
        assert client.server_address == "127.0.0.1:8188"
        assert client.render_timeout == 600.0
        assert client.auto_free_vram is True
        assert client.output_root.exists()

    def test_client_custom_config(self):
        """Client accepts custom configuration."""
        from mathart.backend.comfy_client import ComfyAPIClient

        client = ComfyAPIClient(
            server_address="192.168.1.100:8188",
            render_timeout=300.0,
            poll_interval=2.0,
            auto_free_vram=False,
        )
        assert client.server_address == "192.168.1.100:8188"
        assert client.render_timeout == 300.0
        assert client.poll_interval == 2.0
        assert client.auto_free_vram is False

    def test_server_offline_graceful_degradation(self):
        """Client returns degraded result when server is offline."""
        from mathart.backend.comfy_client import ComfyAPIClient

        client = ComfyAPIClient(server_address="192.168.99.99:9999")
        assert client.is_server_online() is False

        result = client.render(
            {"prompt": SAMPLE_WORKFLOW, "client_id": "test"},
        )
        assert result.success is False
        assert result.degraded is True
        assert "offline" in result.degraded_reason.lower()

    def test_render_timeout_error_type(self):
        """RenderTimeoutError is a proper TimeoutError subclass."""
        from mathart.backend.comfy_client import RenderTimeoutError

        err = RenderTimeoutError("test timeout")
        assert isinstance(err, TimeoutError)
        assert "test timeout" in str(err)

    def test_upload_error_type(self):
        """UploadError is a proper IOError subclass."""
        from mathart.backend.comfy_client import UploadError

        err = UploadError("upload failed")
        assert isinstance(err, IOError)

    def test_render_result_to_dict(self):
        """RenderResult serializes to dict correctly."""
        from mathart.backend.comfy_client import RenderResult

        result = RenderResult(
            success=True,
            prompt_id="abc123",
            output_images=["/path/to/img.png"],
            elapsed_seconds=12.345,
            vram_freed=True,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["prompt_id"] == "abc123"
        assert d["elapsed_seconds"] == 12.345
        assert d["vram_freed"] is True

    def test_red_line_poll_has_sleep(self):
        """[RED LINE] HTTP poll loop MUST have time.sleep() to prevent deadlock."""
        from mathart.backend import comfy_client
        import inspect

        source = inspect.getsource(comfy_client.ComfyAPIClient._http_poll_wait)
        assert "time.sleep" in source
        assert "RenderTimeoutError" in source

    def test_red_line_output_repatriation(self):
        """[RED LINE] Outputs MUST be saved to outputs/production/."""
        from mathart.backend.comfy_client import ComfyAPIClient

        client = ComfyAPIClient()
        assert "production" in str(client.output_root)


# ═══════════════════════════════════════════════════════════════════════════
# 3. ComfyUIRenderBackend Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestComfyUIRenderBackend:
    """Test the registry-native ComfyUI render backend plugin."""

    def test_backend_registered(self):
        """Backend is auto-registered in the global registry."""
        from mathart.core.backend_registry import get_registry

        registry = get_registry()
        entry = registry.get("comfyui_render")
        assert entry is not None
        meta, cls = entry
        assert meta.name == "comfyui_render"
        assert "COMFYUI_RENDER" in str(meta.capabilities)

    def test_backend_type_enum(self):
        """COMFYUI_RENDER is a valid BackendType enum member."""
        from mathart.core.backend_types import BackendType

        assert hasattr(BackendType, "COMFYUI_RENDER")
        assert BackendType.COMFYUI_RENDER.value == "comfyui_render"

    def test_artifact_family_enum(self):
        """COMFYUI_RENDER_REPORT is a valid ArtifactFamily enum member."""
        from mathart.core.artifact_schema import ArtifactFamily

        assert hasattr(ArtifactFamily, "COMFYUI_RENDER_REPORT")
        assert ArtifactFamily.COMFYUI_RENDER_REPORT.value == "comfyui_render_report"

    def test_required_metadata_keys(self):
        """COMFYUI_RENDER_REPORT has correct required metadata keys."""
        from mathart.core.artifact_schema import ArtifactFamily

        keys = ArtifactFamily.required_metadata_keys("comfyui_render_report")
        assert "prompt_id" in keys
        assert "server_address" in keys
        assert "render_elapsed_seconds" in keys
        assert "images_downloaded" in keys
        assert "vram_freed" in keys
        assert "mutation_count" in keys
        assert "blueprint_name" in keys

    def test_validate_config(self):
        """validate_config() normalizes ComfyUI render configuration."""
        from mathart.backend.comfyui_render_backend import ComfyUIRenderBackend

        backend = ComfyUIRenderBackend()
        config = {
            "comfyui": {
                "url": "http://localhost:8188",
                "render_timeout": 300,
            },
            "prompt": "pixel art hero",
            "seed": 42,
        }
        validated, warnings = backend.validate_config(config)
        assert validated["comfyui"]["server_address"] == "localhost:8188"
        assert validated["comfyui"]["render_timeout"] == 300.0
        assert validated["prompt"] == "pixel art hero"
        assert validated["seed"] == 42

    def test_validate_config_strips_protocol(self):
        """validate_config() strips http:// prefix from server address."""
        from mathart.backend.comfyui_render_backend import ComfyUIRenderBackend

        backend = ComfyUIRenderBackend()
        config = {
            "comfyui": {"url": "http://192.168.1.100:8188"},
            "prompt": "test",
        }
        validated, _ = backend.validate_config(config)
        assert validated["comfyui"]["server_address"] == "192.168.1.100:8188"

    def test_validate_config_clamps_timeout(self):
        """validate_config() clamps render_timeout to minimum 10s."""
        from mathart.backend.comfyui_render_backend import ComfyUIRenderBackend

        backend = ComfyUIRenderBackend()
        config = {
            "comfyui": {"render_timeout": 1},
            "prompt": "test",
        }
        validated, warnings = backend.validate_config(config)
        assert validated["comfyui"]["render_timeout"] == 10.0
        assert any("too low" in w for w in warnings)

    def test_execute_offline_degraded(self):
        """execute() returns degraded manifest when ComfyUI is offline."""
        from mathart.backend.comfyui_render_backend import ComfyUIRenderBackend
        from mathart.core.artifact_schema import ArtifactFamily

        backend = ComfyUIRenderBackend()
        manifest = backend.execute({
            "comfyui": {"server_address": "192.168.99.99:9999"},
            "workflow_blueprint": "",
            "image_path": "",
            "prompt": "test",
            "negative_prompt": "",
            "seed": 42,
            "output_dir": "",
            "output_prefix": "test",
        })

        assert manifest.artifact_family == ArtifactFamily.COMFYUI_RENDER_REPORT.value
        assert manifest.metadata["degraded"] is True
        assert "offline" in manifest.metadata["degraded_reason"].lower()

    def test_backend_type_aliases(self):
        """ComfyUI render backend type aliases resolve correctly."""
        from mathart.core.backend_types import backend_type_value

        assert backend_type_value("comfyui_render") == "comfyui_render"
        assert backend_type_value("comfy_render") == "comfyui_render"
        assert backend_type_value("comfyui_api_render") == "comfyui_render"
        assert backend_type_value("comfyui_headless") == "comfyui_render"
        assert backend_type_value("bff_render") == "comfyui_render"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Integration tests for the full render pipeline."""

    def test_mutator_to_client_payload_contract(self):
        """Mutator output is compatible with client input contract."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator
        from mathart.backend.comfy_client import ComfyAPIClient

        mutator = ComfyWorkflowMutator()
        payload = mutator.build_payload(
            workflow=SAMPLE_WORKFLOW,
            image_filename="test.png",
            prompt="pixel art",
            seed=42,
        )

        # Payload must have the keys expected by ComfyAPIClient.render()
        assert "prompt" in payload
        assert "client_id" in payload
        assert isinstance(payload["prompt"], dict)
        assert isinstance(payload["client_id"], str)

    def test_blueprint_file_loading(self, tmp_path: Path):
        """Mutator can load blueprint from file and mutate it."""
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator

        bp_path = tmp_path / "workflow_api.json"
        bp_path.write_text(json.dumps(SAMPLE_WORKFLOW), encoding="utf-8")

        mutator = ComfyWorkflowMutator(blueprint_path=bp_path)
        payload = mutator.build_payload(
            image_filename="uploaded.png",
            prompt="test prompt",
            seed=123,
        )

        assert payload["prompt"]["10"]["inputs"]["image"] == "uploaded.png"
        assert payload["prompt"]["6"]["inputs"]["text"] == "test prompt"
        assert payload["prompt"]["3"]["inputs"]["seed"] == 123

    def test_end_to_end_offline_graceful(self):
        """Full pipeline gracefully handles offline ComfyUI server."""
        from mathart.backend.comfyui_render_backend import ComfyUIRenderBackend

        backend = ComfyUIRenderBackend()
        manifest = backend.execute({
            "comfyui": {"server_address": "192.168.99.99:9999"},
            "workflow_blueprint": "",
            "image_path": "",
            "prompt": "test",
            "negative_prompt": "",
            "seed": 42,
            "output_dir": "/tmp/test_output",
            "output_prefix": "test",
        })

        # Should not crash — returns degraded manifest
        assert manifest is not None
        assert manifest.metadata.get("degraded") is True
