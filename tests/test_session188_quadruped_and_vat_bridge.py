"""SESSION-188: Quadruped Physics Engine Awakening & VAT Real-Data Bridge — Unit Tests.

Tests cover:
  1. QuadrupedPhysicsBackend registration and execution
  2. Quadruped gait solver (trot + pace profiles)
  3. Dynamic reshape for cross-topology VAT feeding
  4. Skeleton topology inference from vibe text
  5. SemanticOrchestrator quadruped trigger map
  6. VAT Backend real-data bridge (positions passthrough)
  7. CreatorIntentSpec skeleton_topology serialization round-trip
  8. End-to-end: Quadruped → VAT pipeline integration
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import numpy as np
import pytest

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  1. Quadruped Physics Backend — Registration & Import
# ═══════════════════════════════════════════════════════════════════════════

class TestQuadrupedPhysicsBackendImport:
    """Verify the quadruped physics backend module is importable."""

    def test_import_module(self):
        from mathart.core.quadruped_physics_backend import (
            QuadrupedPhysicsBackend,
            QuadrupedPhysicsResult,
            QUADRUPED_LIMBS,
            BIPED_LIMBS,
            SKELETON_TOPOLOGIES,
            QUADRUPED_KEYWORDS,
            infer_skeleton_topology,
            solve_quadruped_physics,
            reshape_positions_for_vat,
        )
        assert QuadrupedPhysicsBackend is not None
        assert len(QUADRUPED_LIMBS) == 4
        assert len(BIPED_LIMBS) == 2
        assert "biped" in SKELETON_TOPOLOGIES
        assert "quadruped" in SKELETON_TOPOLOGIES

    def test_backend_registered_in_registry(self):
        """Quadruped physics backend should be discoverable via BackendRegistry."""
        from mathart.core.backend_registry import get_registry
        registry = get_registry()
        all_backends = registry.all_backends()
        assert "quadruped_physics" in all_backends, (
            f"quadruped_physics not found in registry. Available: {sorted(all_backends.keys())}"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  2. Quadruped Gait Solver
# ═══════════════════════════════════════════════════════════════════════════

class TestQuadrupedGaitSolver:
    """Test the quadruped physics solver produces valid output."""

    def test_trot_basic(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(
            num_frames=20,
            num_vertices=32,
            channels=3,
            gait_profile_name="quadruped_trot",
        )
        assert result.frames == 20
        assert result.vertices == 32
        assert result.channels == 3
        assert result.positions is not None
        assert result.positions.shape == (20, 32, 3)
        assert result.topology == "quadruped"
        assert result.gait_type == "quadruped_trot"

    def test_pace_basic(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(
            num_frames=15,
            num_vertices=16,
            channels=3,
            gait_profile_name="quadruped_pace",
        )
        assert result.frames == 15
        assert result.positions.shape == (15, 16, 3)
        assert result.gait_type == "quadruped_pace"

    def test_contact_sequence_populated(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(num_frames=10, num_vertices=16)
        assert len(result.contact_sequence) == 10
        # Each frame should have contact labels for quadruped limbs
        for cs in result.contact_sequence:
            assert isinstance(cs, dict)

    def test_diagonal_error_finite(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(num_frames=30, num_vertices=64)
        assert np.isfinite(result.diagonal_error)
        assert result.diagonal_error >= 0.0

    def test_result_to_dict(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(num_frames=10, num_vertices=16)
        d = result.to_dict()
        assert d["frames"] == 10
        assert d["vertices"] == 16
        assert d["topology"] == "quadruped"
        assert "diagonal_error" in d

    def test_positions_dtype_float64(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        result = solve_quadruped_physics(num_frames=5, num_vertices=8)
        assert result.positions.dtype == np.float64

    def test_reproducibility_with_seed(self):
        from mathart.core.quadruped_physics_backend import solve_quadruped_physics
        r1 = solve_quadruped_physics(num_frames=10, num_vertices=8, seed=123)
        r2 = solve_quadruped_physics(num_frames=10, num_vertices=8, seed=123)
        np.testing.assert_array_equal(r1.positions, r2.positions)


# ═══════════════════════════════════════════════════════════════════════════
#  3. Dynamic Reshape for Cross-Topology VAT Feeding
# ═══════════════════════════════════════════════════════════════════════════

class TestReshapePositionsForVAT:
    """Test dynamic reshape handles topology mismatches."""

    def test_identity_reshape(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 32, 3)
        result = reshape_positions_for_vat(positions, target_vertices=32, target_channels=3)
        np.testing.assert_array_almost_equal(result, positions)

    def test_upsample_vertices(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 16, 3)
        result = reshape_positions_for_vat(positions, target_vertices=64, target_channels=3)
        assert result.shape == (10, 64, 3)

    def test_downsample_vertices(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 64, 3)
        result = reshape_positions_for_vat(positions, target_vertices=16, target_channels=3)
        assert result.shape == (10, 16, 3)

    def test_pad_channels(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 32, 2)
        result = reshape_positions_for_vat(positions, target_vertices=32, target_channels=3)
        assert result.shape == (10, 32, 3)
        # Third channel should be zero-padded
        np.testing.assert_array_equal(result[:, :, 2], 0.0)

    def test_truncate_channels(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 32, 4)
        result = reshape_positions_for_vat(positions, target_vertices=32, target_channels=3)
        assert result.shape == (10, 32, 3)

    def test_invalid_ndim_raises(self):
        from mathart.core.quadruped_physics_backend import reshape_positions_for_vat
        positions = np.random.randn(10, 32)  # 2D, should fail
        with pytest.raises(ValueError, match="Expected 3D"):
            reshape_positions_for_vat(positions, target_vertices=32)


# ═══════════════════════════════════════════════════════════════════════════
#  4. Skeleton Topology Inference
# ═══════════════════════════════════════════════════════════════════════════

class TestSkeletonTopologyInference:
    """Test skeleton topology inference from vibe text."""

    def test_biped_default(self):
        from mathart.core.quadruped_physics_backend import infer_skeleton_topology
        assert infer_skeleton_topology("") == "biped"
        assert infer_skeleton_topology("活泼的跳跃角色") == "biped"
        assert infer_skeleton_topology("一个战士") == "biped"

    def test_quadruped_chinese_keywords(self):
        from mathart.core.quadruped_physics_backend import infer_skeleton_topology
        assert infer_skeleton_topology("四足机械狗") == "quadruped"
        assert infer_skeleton_topology("赛博狗奔跑") == "quadruped"
        assert infer_skeleton_topology("一匹奔跑的马") == "quadruped"
        assert infer_skeleton_topology("凶猛的狼") == "quadruped"

    def test_quadruped_english_keywords(self):
        from mathart.core.quadruped_physics_backend import infer_skeleton_topology
        assert infer_skeleton_topology("a running dog") == "quadruped"
        assert infer_skeleton_topology("quadruped creature") == "quadruped"
        assert infer_skeleton_topology("cyber dog mech") == "quadruped"
        assert infer_skeleton_topology("four-legged beast") == "quadruped"

    def test_case_insensitive(self):
        from mathart.core.quadruped_physics_backend import infer_skeleton_topology
        assert infer_skeleton_topology("QUADRUPED") == "quadruped"
        assert infer_skeleton_topology("Dog") == "quadruped"


# ═══════════════════════════════════════════════════════════════════════════
#  5. SemanticOrchestrator Quadruped Trigger Map
# ═══════════════════════════════════════════════════════════════════════════

class _FakeBackend:
    """Minimal backend stub for registry tests."""
    def __init__(self, name: str):
        self.name = name
        self.display_name = name.replace("_", " ").title()
    def execute(self, context=None):
        return {"status": "ok", "backend": self.name}

class _FakeRegistry:
    """Minimal BackendRegistry stub."""
    def __init__(self, backends=None):
        self._backends = backends or {}
    def all_backends(self):
        return dict(self._backends)
    def get_backend(self, name):
        return self._backends.get(name)

def _make_registry(*names):
    backends = {n: _FakeBackend(n) for n in names}
    return _FakeRegistry(backends)


class TestSemanticOrchestratorQuadruped:
    """Test SemanticOrchestrator quadruped trigger integration."""

    def test_quadruped_trigger_map_exists(self):
        from mathart.workspace.semantic_orchestrator import SEMANTIC_VFX_TRIGGER_MAP
        assert "四足" in SEMANTIC_VFX_TRIGGER_MAP
        assert "quadruped" in SEMANTIC_VFX_TRIGGER_MAP
        assert "机械狗" in SEMANTIC_VFX_TRIGGER_MAP
        assert "dog" in SEMANTIC_VFX_TRIGGER_MAP

    def test_quadruped_plugin_capability_exists(self):
        from mathart.workspace.semantic_orchestrator import VFX_PLUGIN_CAPABILITIES
        assert "quadruped_physics" in VFX_PLUGIN_CAPABILITIES
        cap = VFX_PLUGIN_CAPABILITIES["quadruped_physics"]
        assert "display_name" in cap
        assert "description" in cap

    def test_heuristic_quadruped_trigger(self):
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator
        registry = _make_registry("quadruped_physics", "cppn_texture_evolution")
        orch = SemanticOrchestrator()
        result = orch.resolve_vfx_plugins(
            raw_intent={},
            vibe="四足机械狗",
            registry=registry,
        )
        assert "quadruped_physics" in result

    def test_topology_inference_via_orchestrator(self):
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator
        orch = SemanticOrchestrator()
        assert orch.infer_skeleton_topology("四足机械狗") == "quadruped"
        assert orch.infer_skeleton_topology("活泼的角色") == "biped"

    def test_resolve_full_intent(self):
        from mathart.workspace.semantic_orchestrator import SemanticOrchestrator
        registry = _make_registry("quadruped_physics", "high_precision_vat")
        orch = SemanticOrchestrator()
        result = orch.resolve_full_intent(
            raw_intent={},
            vibe="四足机械狗 高精度导出",
            registry=registry,
        )
        assert result["skeleton_topology"] == "quadruped"
        assert "quadruped_physics" in result["active_vfx_plugins"]
        assert "high_precision_vat" in result["active_vfx_plugins"]


# ═══════════════════════════════════════════════════════════════════════════
#  6. VAT Backend Real-Data Bridge
# ═══════════════════════════════════════════════════════════════════════════

class TestVATBackendRealDataBridge:
    """Test that VAT backend consumes real physics data when provided."""

    def test_vat_backend_with_real_positions(self):
        """When positions are provided, VAT should use them directly."""
        from mathart.core.high_precision_vat_backend import HighPrecisionVATBackend
        backend = HighPrecisionVATBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            real_positions = np.random.randn(10, 32, 3).astype(np.float64)
            manifest = backend.execute({
                "output_dir": tmpdir,
                "positions": real_positions,
                "skeleton_topology": "quadruped",
            })
            assert manifest is not None
            assert manifest.metadata.get("data_source") == "real_physics"
            assert manifest.metadata.get("skeleton_topology") == "quadruped"

    def test_vat_backend_fallback_synthetic(self):
        """When no positions provided, VAT should use Catmull-Rom fallback."""
        from mathart.core.high_precision_vat_backend import HighPrecisionVATBackend
        backend = HighPrecisionVATBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = backend.execute({
                "output_dir": tmpdir,
                "num_frames": 8,
                "num_vertices": 16,
            })
            assert manifest is not None
            assert manifest.metadata.get("data_source") == "synthetic_catmull_rom"
            assert manifest.metadata.get("skeleton_topology") == "biped"


# ═══════════════════════════════════════════════════════════════════════════
#  7. CreatorIntentSpec skeleton_topology Round-Trip
# ═══════════════════════════════════════════════════════════════════════════

class TestCreatorIntentSpecTopology:
    """Test skeleton_topology field in CreatorIntentSpec."""

    def test_default_topology_is_biped(self):
        from mathart.workspace.director_intent import CreatorIntentSpec
        spec = CreatorIntentSpec()
        assert spec.skeleton_topology == "biped"

    def test_quadruped_topology_serialization(self):
        from mathart.workspace.director_intent import CreatorIntentSpec
        spec = CreatorIntentSpec(skeleton_topology="quadruped")
        d = spec.to_dict()
        assert d["skeleton_topology"] == "quadruped"

    def test_topology_round_trip(self):
        from mathart.workspace.director_intent import CreatorIntentSpec
        spec = CreatorIntentSpec(
            skeleton_topology="quadruped",
            raw_vibe="四足机械狗",
            active_vfx_plugins=["quadruped_physics"],
        )
        d = spec.to_dict()
        restored = CreatorIntentSpec.from_dict(d)
        assert restored.skeleton_topology == "quadruped"
        assert restored.raw_vibe == "四足机械狗"
        assert "quadruped_physics" in restored.active_vfx_plugins

    def test_backward_compatible_from_dict(self):
        """Old dicts without skeleton_topology should default to 'biped'."""
        from mathart.workspace.director_intent import CreatorIntentSpec
        old_dict = {
            "genotype": {},
            "raw_vibe": "test",
            "active_vfx_plugins": [],
        }
        spec = CreatorIntentSpec.from_dict(old_dict)
        assert spec.skeleton_topology == "biped"


# ═══════════════════════════════════════════════════════════════════════════
#  8. End-to-End: Quadruped → VAT Pipeline Integration
# ═══════════════════════════════════════════════════════════════════════════

class TestQuadrupedToVATEndToEnd:
    """Test the full pipeline: quadruped solver → reshape → VAT baking."""

    def test_quadruped_positions_feed_vat(self):
        from mathart.core.quadruped_physics_backend import (
            solve_quadruped_physics,
            reshape_positions_for_vat,
        )
        from mathart.core.high_precision_vat_backend import HighPrecisionVATBackend

        # Step 1: Solve quadruped physics
        result = solve_quadruped_physics(
            num_frames=12,
            num_vertices=24,
            channels=3,
        )
        assert result.positions.shape == (12, 24, 3)

        # Step 2: Reshape for VAT (target 32 vertices)
        reshaped = reshape_positions_for_vat(
            result.positions,
            target_vertices=32,
            target_channels=3,
        )
        assert reshaped.shape == (12, 32, 3)

        # Step 3: Feed to VAT backend
        backend = HighPrecisionVATBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = backend.execute({
                "output_dir": tmpdir,
                "positions": reshaped,
                "skeleton_topology": "quadruped",
                "num_vertices": 32,
            })
            assert manifest is not None
            assert manifest.metadata["data_source"] == "real_physics"
            assert manifest.metadata["skeleton_topology"] == "quadruped"
            assert manifest.metadata["vertex_count"] == 32
            assert manifest.metadata["frame_count"] == 12

    def test_quadruped_backend_execute(self):
        """Test QuadrupedPhysicsBackend.execute() produces valid manifest."""
        from mathart.core.quadruped_physics_backend import QuadrupedPhysicsBackend
        backend = QuadrupedPhysicsBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = backend.execute({
                "output_dir": tmpdir,
                "num_frames": 10,
                "num_vertices": 16,
                "gait_profile": "quadruped_trot",
            })
            assert manifest is not None
            assert manifest.metadata["topology"] == "quadruped"
            assert manifest.metadata["frame_count"] == 10
            # Check output files exist
            assert Path(manifest.outputs["positions_npy"]).exists()
            assert Path(manifest.outputs["physics_report"]).exists()
