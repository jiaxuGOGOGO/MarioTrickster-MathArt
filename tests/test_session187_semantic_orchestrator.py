"""SESSION-187: Semantic Orchestrator & Pipeline Weaver — Unit Tests.

Tests cover:
  1. SemanticOrchestrator VFX plugin resolution (LLM path + heuristic path)
  2. Hallucination guard (set intersection filtering)
  3. DynamicPipelineWeaver middleware chain execution
  4. Graceful degradation on plugin failure
  5. CreatorIntentSpec active_vfx_plugins serialization round-trip
"""
from __future__ import annotations

import json
import logging
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Fixtures ──────────────────────────────────────────────────────────────

class _FakeBackend:
    """Minimal backend stub for registry tests."""

    def __init__(self, name: str, *, should_fail: bool = False):
        self.name = name
        self._should_fail = should_fail
        self.display_name = name.replace("_", " ").title()

    def execute(self, context: dict | None = None) -> dict:
        if self._should_fail:
            raise RuntimeError(f"Simulated failure in {self.name}")
        return {"status": "ok", "backend": self.name}


class _FakeRegistry:
    """Minimal BackendRegistry stub."""

    def __init__(self, backends: dict[str, _FakeBackend] | None = None):
        self._backends = backends or {}

    def all_backends(self) -> dict:
        return dict(self._backends)

    def get_backend(self, name: str):
        return self._backends.get(name)


def _make_registry(*names: str, fail_names: set[str] | None = None) -> _FakeRegistry:
    fail_names = fail_names or set()
    backends = {}
    for n in names:
        backends[n] = _FakeBackend(n, should_fail=(n in fail_names))
    return _FakeRegistry(backends)


# ─── SemanticOrchestrator Tests ────────────────────────────────────────────

class TestSemanticOrchestrator:
    """Test the SemanticOrchestrator VFX plugin resolution."""

    def test_import(self):
        """Module should be importable."""
        from mathart.workspace.semantic_orchestrator import (
            SemanticOrchestrator,
            VFX_PLUGIN_CAPABILITIES,
            SEMANTIC_VFX_TRIGGER_MAP,
        )
        assert isinstance(VFX_PLUGIN_CAPABILITIES, dict)
        assert isinstance(SEMANTIC_VFX_TRIGGER_MAP, dict)

    def test_heuristic_cppn_trigger(self):
        """Vibe containing '纹理' should activate cppn_texture_evolution."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("cppn_texture_evolution", "fluid_momentum_controller")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="赛博朋克风格的纹理效果",
            registry=registry,
        )
        assert "cppn_texture_evolution" in result

    def test_heuristic_fluid_trigger(self):
        """Vibe containing '水花' should activate fluid_momentum_controller."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("cppn_texture_evolution", "fluid_momentum_controller")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="挥刀水花特效",
            registry=registry,
        )
        assert "fluid_momentum_controller" in result

    def test_heuristic_vat_trigger(self):
        """Vibe containing 'VAT' should activate high_precision_vat."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("high_precision_vat")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="需要 VAT 导出到 Unreal",
            registry=registry,
        )
        assert "high_precision_vat" in result

    def test_heuristic_max_vfx(self):
        """Vibe containing '全特效' should activate all known VFX plugins."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry(
            "cppn_texture_evolution",
            "fluid_momentum_controller",
            "high_precision_vat",
        )
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="黑科技全开",
            registry=registry,
        )
        assert len(result) == 3

    def test_llm_path_valid(self):
        """LLM-suggested plugins that exist in registry should be accepted."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("cppn_texture_evolution", "fluid_momentum_controller")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={"active_vfx_plugins": ["cppn_texture_evolution"]},
            vibe="",
            registry=registry,
        )
        assert result == ["cppn_texture_evolution"]

    def test_hallucination_guard(self):
        """LLM-suggested plugins that do NOT exist in registry should be filtered out."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("cppn_texture_evolution")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={
                "active_vfx_plugins": [
                    "cppn_texture_evolution",
                    "hallucinated_plugin_xyz",
                    "another_fake_plugin",
                ]
            },
            vibe="",
            registry=registry,
        )
        assert result == ["cppn_texture_evolution"]

    def test_empty_vibe_no_plugins(self):
        """Empty vibe with no LLM suggestions should return empty list."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry("cppn_texture_evolution")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="",
            registry=registry,
        )
        assert result == []

    def test_no_registry_backends_returns_empty(self):
        """If registry has no backends, always return empty list."""
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator

        registry = _make_registry()  # empty
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={"active_vfx_plugins": ["cppn_texture_evolution"]},
            vibe="赛博朋克纹理",
            registry=registry,
        )
        assert result == []


# ─── DynamicPipelineWeaver Tests ───────────────────────────────────────────

class TestDynamicPipelineWeaver:
    """Test the DynamicPipelineWeaver middleware chain."""

    def test_import(self):
        """Module should be importable."""
        from mathart.workspace.pipeline_weaver import (
            DynamicPipelineWeaver,
            WeaverResult,
            PipelineObserver,
        )

    def test_execute_all_success(self):
        """All plugins should execute successfully."""
        from mathart.workspace.pipeline_weaver import DynamicPipelineWeaver

        registry = _make_registry("cppn_texture_evolution", "fluid_momentum_controller")
        weaver = DynamicPipelineWeaver(registry=registry)
        result = weaver.execute(
            plugin_names=["cppn_texture_evolution", "fluid_momentum_controller"],
            context={"project_root": "/tmp/test"},
        )
        assert len(result.executed) == 2
        assert len(result.skipped) == 0
        assert len(result.errors) == 0

    def test_graceful_degradation(self):
        """Failed plugins should be skipped without breaking the chain."""
        from mathart.workspace.pipeline_weaver import DynamicPipelineWeaver

        registry = _make_registry(
            "cppn_texture_evolution",
            "fluid_momentum_controller",
            fail_names={"cppn_texture_evolution"},
        )
        weaver = DynamicPipelineWeaver(registry=registry)
        result = weaver.execute(
            plugin_names=["cppn_texture_evolution", "fluid_momentum_controller"],
            context={},
        )
        assert "cppn_texture_evolution" in result.skipped
        assert "fluid_momentum_controller" in result.executed
        assert "cppn_texture_evolution" in result.errors

    def test_unknown_plugin_skipped(self):
        """Plugins not in registry should be skipped gracefully."""
        from mathart.workspace.pipeline_weaver import DynamicPipelineWeaver

        registry = _make_registry("cppn_texture_evolution")
        weaver = DynamicPipelineWeaver(registry=registry)
        result = weaver.execute(
            plugin_names=["cppn_texture_evolution", "nonexistent_plugin"],
            context={},
        )
        assert "cppn_texture_evolution" in result.executed
        assert "nonexistent_plugin" in result.skipped

    def test_empty_plugin_list(self):
        """Empty plugin list should return a clean result."""
        from mathart.workspace.pipeline_weaver import DynamicPipelineWeaver

        registry = _make_registry("cppn_texture_evolution")
        weaver = DynamicPipelineWeaver(registry=registry)
        result = weaver.execute(plugin_names=[], context={})
        assert result.executed == []
        assert result.skipped == []
        assert result.total_ms >= 0

    def test_observer_callbacks(self):
        """Observer should receive start/done/error callbacks."""
        from mathart.workspace.pipeline_weaver import (
            DynamicPipelineWeaver,
            PipelineObserver,
        )

        registry = _make_registry(
            "cppn_texture_evolution",
            "fluid_momentum_controller",
            fail_names={"fluid_momentum_controller"},
        )
        observer = PipelineObserver()
        starts = []
        dones = []
        errors = []

        observer.on_plugin_start = lambda **kw: starts.append(kw)
        observer.on_plugin_done = lambda **kw: dones.append(kw)
        observer.on_plugin_error = lambda **kw: errors.append(kw)

        weaver = DynamicPipelineWeaver(registry=registry, observer=observer)
        weaver.execute(
            plugin_names=["cppn_texture_evolution", "fluid_momentum_controller"],
            context={},
        )
        assert len(starts) == 2
        assert len(dones) == 2  # both get done callback
        assert len(errors) == 1  # only the failed one


# ─── CreatorIntentSpec Round-Trip Tests ────────────────────────────────────

class TestCreatorIntentSpecVFX:
    """Test active_vfx_plugins field serialization."""

    def test_to_dict_includes_vfx(self):
        """to_dict() should include active_vfx_plugins."""
        from mathart.workspace.director_intent import CreatorIntentSpec

        spec = CreatorIntentSpec.__new__(CreatorIntentSpec)
        # Set minimal required attributes
        spec.vibe = "test"
        spec.description = ""
        spec.base_blueprint = None
        spec.overrides = {}
        spec.evolve_variants = 0
        spec.freeze_locks = []
        spec.genotype = None
        spec.active_vfx_plugins = ["cppn_texture_evolution"]
        # Additional attributes that may exist
        for attr in ("knowledge_provenance", "applied_knowledge_rules",
                     "knowledge_clamp_report", "distillation_summary"):
            if not hasattr(spec, attr):
                setattr(spec, attr, None)

        d = spec.to_dict()
        assert "active_vfx_plugins" in d
        assert d["active_vfx_plugins"] == ["cppn_texture_evolution"]

    def test_from_dict_reads_vfx(self):
        """from_dict() should read active_vfx_plugins."""
        from mathart.workspace.director_intent import CreatorIntentSpec

        data = {
            "vibe": "test",
            "description": "",
            "active_vfx_plugins": ["fluid_momentum_controller"],
        }
        spec = CreatorIntentSpec.from_dict(data)
        assert hasattr(spec, "active_vfx_plugins")
        assert spec.active_vfx_plugins == ["fluid_momentum_controller"]

    def test_from_dict_default_empty(self):
        """from_dict() without active_vfx_plugins should default to []."""
        from mathart.workspace.director_intent import CreatorIntentSpec

        data = {"vibe": "test", "description": ""}
        spec = CreatorIntentSpec.from_dict(data)
        assert hasattr(spec, "active_vfx_plugins")
        assert spec.active_vfx_plugins == []


# ─── Anti-Hardcoded Verification ───────────────────────────────────────────

class TestAntiHardcoded:
    """Verify zero hardcoded plugin name branches in pipeline_weaver.py."""

    def test_no_hardcoded_plugin_names_in_weaver(self):
        """pipeline_weaver.py should contain zero if/elif branches for specific plugin names."""
        weaver_path = Path(__file__).parent.parent / "mathart" / "workspace" / "pipeline_weaver.py"
        if not weaver_path.exists():
            pytest.skip("pipeline_weaver.py not found")

        source = weaver_path.read_text(encoding="utf-8")
        # These patterns would indicate hardcoded plugin routing
        forbidden_patterns = [
            'if "cppn"',
            'if "fluid"',
            'if "vat"',
            "elif \"cppn\"",
            "elif \"fluid\"",
            "elif \"vat\"",
            'if name == "cppn',
            'if name == "fluid',
            'if name == "high_precision',
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"Anti-Hardcoded violation: found '{pattern}' in pipeline_weaver.py"
            )
