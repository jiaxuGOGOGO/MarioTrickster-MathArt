"""Tests for SESSION-183: Microkernel Laboratory Hub & High-Precision VAT Backend.

Validates:
1. Laboratory Hub reflection-based backend discovery
2. High-Precision VAT Backend registration and execution
3. Sandboxed output isolation (workspace/laboratory/)
4. Zero-pollution-to-production-vault guarantee
5. Dynamic menu generation via Python reflection
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# ── Ensure project root is on sys.path ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: BackendRegistry discovers High-Precision VAT Backend
# ═══════════════════════════════════════════════════════════════════════════

def test_vat_backend_registered():
    """The high_precision_vat backend MUST be discoverable via the registry."""
    from mathart.core.backend_registry import get_registry
    # Ensure the VAT backend module is imported (triggers @register_backend)
    import mathart.core.high_precision_vat_backend  # noqa: F401
    registry = get_registry()
    all_backends = registry.all_backends()
    assert "high_precision_vat" in all_backends, (
        "high_precision_vat backend not found in registry. "
        f"Available: {list(all_backends.keys())}"
    )
    meta, cls = all_backends["high_precision_vat"]
    assert meta.display_name == "High-Precision Float VAT Baking (P1-VAT-PRECISION-1)"
    assert meta.version == "1.0.0"
    assert meta.session_origin == "SESSION-183"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: Laboratory Hub discovers ALL registered backends via reflection
# ═══════════════════════════════════════════════════════════════════════════

def test_laboratory_hub_discovers_all_backends():
    """Laboratory Hub MUST discover all registered backends via reflection."""
    from mathart.core.backend_registry import get_registry
    from mathart.laboratory_hub import _discover_lab_backends

    import mathart.core.high_precision_vat_backend  # noqa: F401
    registry = get_registry()
    backends = _discover_lab_backends(registry)

    # Must find at least the VAT backend
    names = [name for name, _, _ in backends]
    assert "high_precision_vat" in names, (
        f"VAT backend not discovered. Found: {names}"
    )

    # All entries must have valid meta and class
    for name, meta, cls in backends:
        assert meta.display_name, f"Backend {name} has empty display_name"
        assert cls is not None, f"Backend {name} has None class"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: Backend summary extraction via __doc__ reflection
# ═══════════════════════════════════════════════════════════════════════════

def test_extract_backend_summary_from_docstring():
    """_extract_backend_summary MUST read class __doc__ via reflection."""
    from mathart.laboratory_hub import _extract_backend_summary

    class FakeBackend:
        """Industrial-grade High-Precision Float VAT baking backend.

        Extended description here.
        """
        pass

    summary = _extract_backend_summary(FakeBackend)
    assert "Industrial-grade" in summary
    assert "Extended" not in summary  # Only first line


def test_extract_backend_summary_no_docstring():
    """_extract_backend_summary falls back to class name when no __doc__."""
    from mathart.laboratory_hub import _extract_backend_summary

    class NoDocBackend:
        pass

    summary = _extract_backend_summary(NoDocBackend)
    assert "NoDocBackend" in summary


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: Sandboxed output directory isolation
# ═══════════════════════════════════════════════════════════════════════════

def test_sandboxed_output_isolation():
    """All laboratory outputs MUST go to workspace/laboratory/<backend>/."""
    from mathart.laboratory_hub import _resolve_lab_output_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        lab_dir = _resolve_lab_output_dir(root, "high_precision_vat")

        expected = root / "workspace" / "laboratory" / "high_precision_vat"
        assert lab_dir == expected
        assert lab_dir.exists()

        # Production vault MUST NOT exist
        production_vault = root / "output" / "production"
        assert not production_vault.exists(), (
            "Production vault was created during laboratory run!"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: VAT Backend standalone execution
# ═══════════════════════════════════════════════════════════════════════════

def test_vat_backend_execute_standalone():
    """VAT backend MUST execute successfully and produce VAT_BUNDLE manifest."""
    from mathart.core.backend_registry import get_registry
    import mathart.core.high_precision_vat_backend  # noqa: F401
    registry = get_registry()
    all_backends = registry.all_backends()
    meta, cls = all_backends["high_precision_vat"]

    with tempfile.TemporaryDirectory() as tmpdir:
        context = {
            "output_dir": tmpdir,
            "verbose": True,
            "num_frames": 8,
            "num_vertices": 16,
            "channels": 3,
        }
        instance = cls()
        manifest = instance.execute(context)

        # Verify manifest structure
        assert manifest.artifact_family == "vat_bundle"
        assert manifest.backend_type == "high_precision_vat"
        assert "position_tex" in manifest.outputs
        assert "manifest" in manifest.outputs
        assert manifest.metadata["frame_count"] == 8
        assert manifest.metadata["vertex_count"] == 16
        assert manifest.metadata["precision"] == "float32"

        # Verify output files exist
        out_dir = Path(tmpdir)
        assert (out_dir / "vat_manifest.json").exists()
        assert (out_dir / "vat_execution_report.json").exists()


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: Catmull-Rom physics sequence generator
# ═══════════════════════════════════════════════════════════════════════════

def test_catmull_rom_physics_sequence():
    """Synthetic physics sequence MUST have correct shape and dtype."""
    from mathart.core.high_precision_vat_backend import (
        _generate_catmull_rom_physics_sequence,
    )

    positions = _generate_catmull_rom_physics_sequence(
        num_frames=12, num_vertices=32, channels=3, seed=42,
    )

    assert positions.shape == (12, 32, 3)
    assert positions.dtype == np.float64
    # Must be deterministic
    positions2 = _generate_catmull_rom_physics_sequence(
        num_frames=12, num_vertices=32, channels=3, seed=42,
    )
    np.testing.assert_array_equal(positions, positions2)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: Zero-pollution guarantee — production vault never touched
# ═══════════════════════════════════════════════════════════════════════════

def test_zero_pollution_production_vault():
    """Executing VAT backend in lab mode MUST NOT create output/production/."""
    from mathart.laboratory_hub import (
        _resolve_lab_output_dir,
        _execute_backend_standalone,
    )
    from mathart.core.backend_registry import get_registry

    import mathart.core.high_precision_vat_backend  # noqa: F401
    registry = get_registry()
    all_backends = registry.all_backends()
    meta, cls = all_backends["high_precision_vat"]

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        lab_dir = _resolve_lab_output_dir(root, "high_precision_vat")

        result = _execute_backend_standalone(
            cls, meta, lab_dir,
            output_fn=lambda x: None,
            verbose=False,
        )

        assert result["status"] == "success"
        # Production vault MUST NOT exist
        assert not (root / "output" / "production").exists()
        # Laboratory sandbox MUST have files
        assert any(lab_dir.iterdir())


# ═══════════════════════════════════════════════════════════════════════════
#  Test 8: Dynamic menu generation — ZERO hardcoded routing
# ═══════════════════════════════════════════════════════════════════════════

def test_dynamic_menu_no_hardcoded_routing():
    """Laboratory Hub menu MUST be 100% reflection-driven, ZERO hardcoded."""
    import inspect
    from mathart.laboratory_hub import run_laboratory_hub

    source = inspect.getsource(run_laboratory_hub)
    # Must NOT contain hardcoded backend routing
    assert "if choice == \"vat\"" not in source.lower()
    assert "if choice == \"high_precision\"" not in source.lower()
    # Must use dynamic route_dict pattern
    assert "route_dict" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
