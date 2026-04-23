"""Tests for AI Render Stream Backend — SESSION-163 (P0-SESSION-161-COMFYUI-API-BRIDGE).

Validates:
1. Backend self-registration via @register_backend
2. Circuit breaker state machine (CLOSED → OPEN → HALF_OPEN)
3. Exponential backoff delay calculation
4. Graceful degradation when ComfyUI is offline
5. Config validation and normalization
6. ArtifactManifest output contract (AI_RENDER_STREAM_REPORT)
7. Red-line audits: no hardcoded paths, no crash on ConnectionRefused
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Registry Registration
# ---------------------------------------------------------------------------

class TestRegistryRegistration:
    """Verify the AI render stream backend self-registers correctly."""

    def test_backend_type_exists(self):
        """AI_RENDER_STREAM must be a valid BackendType."""
        from mathart.core.backend_types import BackendType
        assert hasattr(BackendType, "AI_RENDER_STREAM")
        assert BackendType.AI_RENDER_STREAM.value == "ai_render_stream"

    def test_artifact_family_exists(self):
        """AI_RENDER_STREAM_REPORT must be a valid ArtifactFamily."""
        from mathart.core.artifact_schema import ArtifactFamily
        assert hasattr(ArtifactFamily, "AI_RENDER_STREAM_REPORT")
        assert ArtifactFamily.AI_RENDER_STREAM_REPORT.value == "ai_render_stream_report"

    def test_capability_exists(self):
        """AI_RENDER_STREAM must be a valid BackendCapability."""
        from mathart.core.backend_registry import BackendCapability
        assert hasattr(BackendCapability, "AI_RENDER_STREAM")

    def test_backend_aliases(self):
        """AI render stream aliases must resolve to the canonical value."""
        from mathart.core.backend_types import backend_type_value
        assert backend_type_value("ai_render_stream") == "ai_render_stream"
        assert backend_type_value("ai_render_hydration") == "ai_render_stream"
        assert backend_type_value("render_stream") == "ai_render_stream"
        assert backend_type_value("artifact_hydration") == "ai_render_stream"
        assert backend_type_value("full_array_render") == "ai_render_stream"

    def test_required_metadata_keys(self):
        """AI_RENDER_STREAM_REPORT must enforce mandatory metadata."""
        from mathart.core.artifact_schema import ArtifactFamily
        keys = ArtifactFamily.required_metadata_keys("ai_render_stream_report")
        assert "session_id" in keys
        assert "server_address" in keys
        assert "total_actions" in keys
        assert "total_rendered" in keys
        assert "total_degraded" in keys
        assert "render_elapsed_seconds" in keys


# ---------------------------------------------------------------------------
# 2. Circuit Breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Verify the circuit breaker state machine."""

    def test_initial_state_is_closed(self):
        from mathart.backend.ai_render_stream_backend import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        from mathart.backend.ai_render_stream_backend import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_half_open_after_recovery(self):
        from mathart.backend.ai_render_stream_backend import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_success_resets_to_closed(self):
        from mathart.backend.ai_render_stream_backend import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_to_dict(self):
        from mathart.backend.ai_render_stream_backend import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        d = cb.to_dict()
        assert d["failure_threshold"] == 5
        assert d["recovery_timeout"] == 60.0
        assert d["state"] == "CLOSED"


# ---------------------------------------------------------------------------
# 3. Exponential Backoff
# ---------------------------------------------------------------------------

class TestExponentialBackoff:
    """Verify backoff delay calculation."""

    def test_delay_increases_with_attempt(self):
        from mathart.backend.ai_render_stream_backend import _backoff_delay
        delays = [_backoff_delay(i) for i in range(5)]
        # Delays should generally increase (with jitter they might not be
        # strictly monotonic, but the base should grow)
        assert delays[-1] >= delays[0]

    def test_delay_capped_at_max(self):
        from mathart.backend.ai_render_stream_backend import _backoff_delay, _BACKOFF_MAX_SECONDS
        delay = _backoff_delay(100)
        assert delay <= _BACKOFF_MAX_SECONDS + 2.0  # jitter margin


# ---------------------------------------------------------------------------
# 4. Graceful Degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Verify the backend degrades gracefully when ComfyUI is offline."""

    def test_offline_returns_degraded_manifest(self):
        from mathart.backend.ai_render_stream_backend import AIRenderStreamBackend
        backend = AIRenderStreamBackend()
        context = {
            "artifacts": {"run": {"albedo": "/tmp/fake.png"}},
            "output_dir": "/tmp/test_output",
            "comfyui": {"server_address": "127.0.0.1:9999"},
        }
        # ComfyUI is not running → should degrade gracefully
        manifest = backend.execute(context)
        assert manifest.metadata.get("degraded") is True or manifest.quality_metrics.get("degraded") is True

    def test_no_traceback_on_connection_refused(self):
        """RED LINE: ConnectionRefusedError must NEVER cause a Traceback crash."""
        from mathart.backend.ai_render_stream_backend import AIRenderStreamBackend
        backend = AIRenderStreamBackend()
        context = {
            "artifacts": {},
            "output_dir": "/tmp/test_output",
            "comfyui": {"server_address": "0.0.0.0:1"},
        }
        # This must NOT raise — it must return a valid manifest
        manifest = backend.execute(context)
        assert manifest is not None


# ---------------------------------------------------------------------------
# 5. Config Validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    """Verify config normalization."""

    def test_strips_protocol_prefix(self):
        from mathart.backend.ai_render_stream_backend import AIRenderStreamBackend
        backend = AIRenderStreamBackend()
        config = {"comfyui": {"server_address": "http://192.168.1.100:8188/"}}
        validated, warnings = backend.validate_config(config)
        assert validated["comfyui"]["server_address"] == "192.168.1.100:8188"

    def test_clamps_low_timeout(self):
        from mathart.backend.ai_render_stream_backend import AIRenderStreamBackend
        backend = AIRenderStreamBackend()
        config = {"comfyui": {"render_timeout": 3}}
        validated, warnings = backend.validate_config(config)
        assert validated["comfyui"]["render_timeout"] == 10.0
        assert any("too low" in w for w in warnings)

    def test_default_blueprint_resolution(self):
        from mathart.backend.ai_render_stream_backend import AIRenderStreamBackend
        backend = AIRenderStreamBackend()
        config = {}
        validated, _ = backend.validate_config(config)
        bp = validated["workflow_blueprint"]
        if bp:
            assert "workflow_api_template.json" in bp


# ---------------------------------------------------------------------------
# 6. Workflow Template
# ---------------------------------------------------------------------------

class TestWorkflowTemplate:
    """Verify the bundled workflow template exists and is valid."""

    def test_template_exists(self):
        template_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "assets" / "workflows" / "workflow_api_template.json"
        )
        assert template_path.exists(), f"Template not found: {template_path}"

    def test_template_is_valid_json(self):
        import json
        template_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "assets" / "workflows" / "workflow_api_template.json"
        )
        data = json.loads(template_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_template_has_controlnet_nodes(self):
        """Template MUST contain ControlNet and KSampler nodes."""
        import json
        template_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "assets" / "workflows" / "workflow_api_template.json"
        )
        data = json.loads(template_path.read_text(encoding="utf-8"))
        class_types = {v.get("class_type", "") for v in data.values() if isinstance(v, dict)}
        assert "ControlNetLoader" in class_types
        assert "KSampler" in class_types
        assert "ControlNetApplyAdvanced" in class_types

    def test_template_has_semantic_markers(self):
        """Template MUST contain MathArt semantic markers."""
        import json
        template_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "assets" / "workflows" / "workflow_api_template.json"
        )
        data = json.loads(template_path.read_text(encoding="utf-8"))
        titles = []
        for v in data.values():
            if isinstance(v, dict):
                meta = v.get("_meta", {})
                if isinstance(meta, dict):
                    title = meta.get("title", "")
                    if title:
                        titles.append(title)
        title_text = " ".join(titles)
        assert "[MathArt_Input_Image]" in title_text
        assert "[MathArt_Prompt]" in title_text
        assert "[MathArt_Seed]" in title_text
        assert "[MathArt_Output]" in title_text


# ---------------------------------------------------------------------------
# 7. Red-Line Audits
# ---------------------------------------------------------------------------

class TestRedLineAudits:
    """Enforce SESSION-163 red lines via static analysis."""

    def test_no_hardcoded_absolute_paths(self):
        """RED LINE: No hardcoded absolute paths in the backend source."""
        source_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "backend" / "ai_render_stream_backend.py"
        )
        source = source_path.read_text(encoding="utf-8")
        # Check for Windows-style hardcoded paths
        assert "C:\\Users" not in source
        assert "C:/Users" not in source
        # Check for Unix-style hardcoded paths (excluding docstrings/comments)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            # Actual assignment with hardcoded /home/ or /tmp/ paths
            if "= \"/home/" in stripped or "= '/home/" in stripped:
                pytest.fail(f"Hardcoded absolute path found: {stripped}")

    def test_connection_refused_handled(self):
        """RED LINE: ConnectionRefusedError must be caught in source."""
        source_path = (
            Path(__file__).resolve().parents[1]
            / "mathart" / "backend" / "ai_render_stream_backend.py"
        )
        source = source_path.read_text(encoding="utf-8")
        assert "ConnectionRefusedError" in source
