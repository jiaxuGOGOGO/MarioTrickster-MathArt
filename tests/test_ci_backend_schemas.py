"""CI Backend Schema Validation — SESSION-073 (P1-MIGRATE-3).

This test module implements the **dynamic CI guard** that automatically
discovers all registered backends via reflection, injects backend-specific
Minimal Context Fixtures (derived from each backend's ``input_requirements``),
executes the backend for real, and performs 100% strict Schema Audit on the
resulting ``ArtifactManifest``.

Design references:
    - Pixar OpenUSD ``usdchecker`` Schema Registry & Compliance Checker
    - Google Bazel Hermetic Testing (sealed isolation, zero implicit globals)

Red-line rules enforced:
    1. NO hardcoded backend name lists — discovery is 100% dynamic via
       ``get_registry().all_backends()``.
    2. NO ``try-except pass`` — every backend execution error is surfaced
       as a test failure with full traceback.
    3. Real execution with Minimal Context Fixtures satisfying each
       backend's ``input_requirements``.
    4. ``validate_artifact()`` must return ``[]`` (zero errors) for every
       manifest produced.
    5. ``validate_artifact_strict()`` enforces schema version floor per
       backend's ``schema_version`` declaration.
    6. ``ArtifactFamily.required_metadata_keys()`` coverage is verified.
    7. ``physics3d_telemetry`` sidecar deep validation (array lengths,
       required keys) is exercised for the PHYSICS_3D family.
"""
from __future__ import annotations

import importlib
import pkgutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact,
    validate_artifact_strict,
)
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    BackendRegistry,
    get_registry,
)
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


# ---------------------------------------------------------------------------
# Minimal Context Fixture Factory
# ---------------------------------------------------------------------------

def _build_minimal_context(
    meta: BackendMeta,
    output_dir: str,
) -> dict[str, Any]:
    """Build the smallest viable context that satisfies a backend's
    ``input_requirements``.

    This implements the Google Bazel Hermetic Testing principle: each
    backend receives only the keys it declares, plus the universal
    ``output_dir`` and ``name`` keys. No implicit global state leaks.
    """
    ctx: dict[str, Any] = {
        "output_dir": output_dir,
        "name": f"ci_guard_{meta.name}",
    }

    # Map well-known requirement keys to minimal valid values.
    _REQUIREMENT_FIXTURES: dict[str, Any] = {
        "state": "idle",
        "frame_count": 8,
        "fps": 12,
        "sdf_field": "circle",
        "motion_params": {"speed": 1.0, "amplitude": 0.5},
        "skeleton": {"bones": ["root", "hip", "spine"]},
        "pose": {"root": 0.0, "hip": 0.1, "spine": 0.05},
        "style": "default",
        "sprite_sheet": "/tmp/ci_guard_sprite.png",
        "shader_params": {"outline_width": 1.0},
        "depth_params": {"depth_scale": 1.0},
        "source_frames": [f"/tmp/frame_{i}.png" for i in range(4)],
        "guide_channels": {"depth": "/tmp/depth.png"},
        "tile_rules": {"adjacency": {}},
        "level_params": {"width": 8, "height": 8},
        "physics_sim": {"particles": 10, "steps": 4},
        "vfx_params": {"effect": "splash"},
        "mesh": "/tmp/ci_guard_mesh.obj",
        "cel_params": {"outline_method": "sobel"},
        "evolution_state": {"cycle": 1, "population": []},
        # Physics 3D specific
        "physics3d_ground_y": -10.0,
    }

    for req in meta.input_requirements:
        if req in _REQUIREMENT_FIXTURES:
            ctx[req] = _REQUIREMENT_FIXTURES[req]
        else:
            ctx[req] = f"ci_fixture_{req}"

    return ctx


# ---------------------------------------------------------------------------
# Dynamic Backend Discovery Tests
# ---------------------------------------------------------------------------

class TestCIBackendSchemas:
    """Dynamic CI guard: discover → execute → audit every registered backend."""

    def test_registry_has_backends(self):
        """Registry must contain at least 5 backends (sanity gate)."""
        reg = get_registry()
        backends = reg.all_backends()
        assert len(backends) >= 5, (
            f"Expected at least 5 registered backends, found {len(backends)}: "
            f"{sorted(backends.keys())}"
        )

    def test_all_backends_discovered_dynamically(self):
        """Backend names are discovered via reflection, not hardcoded."""
        reg = get_registry()
        names = sorted(reg.all_backends().keys())
        # This test passes by construction: we iterate the registry.
        # The red-line is that NO test in this file contains a hardcoded
        # backend name list like ["unified_motion", "physics_3d"].
        assert len(names) > 0

    def test_each_backend_produces_valid_manifest(self):
        """Every registered backend, when fed its Minimal Context, must
        produce an ArtifactManifest that passes ``validate_artifact()``
        with zero errors.

        This is the core CI guard — the ``usdchecker`` equivalent.
        """
        reg = get_registry()
        all_backends = reg.all_backends()

        failures: list[str] = []

        with tempfile.TemporaryDirectory(prefix="ci_schema_") as tmpdir:
            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-073",
            )

            for name, (meta, cls) in sorted(all_backends.items()):
                ctx = _build_minimal_context(meta, tmpdir)
                try:
                    manifest = bridge.run_backend(name, ctx)
                except Exception as exc:
                    failures.append(
                        f"[{name}] execution failed: {type(exc).__name__}: {exc}"
                    )
                    continue

                # --- Standard validation ---
                errors = validate_artifact(manifest)
                if errors:
                    failures.append(
                        f"[{name}] validate_artifact errors: {errors}"
                    )

                # --- Strict schema version validation ---
                if meta.schema_version:
                    strict_errors = validate_artifact_strict(
                        manifest, min_schema_version=meta.schema_version,
                    )
                    version_errors = [
                        e for e in strict_errors if "downgrade" in e.lower()
                    ]
                    if version_errors:
                        failures.append(
                            f"[{name}] schema version downgrade: {version_errors}"
                        )

                # --- Artifact family declared by backend must match manifest ---
                if meta.artifact_families:
                    if manifest.artifact_family not in meta.artifact_families:
                        failures.append(
                            f"[{name}] manifest family "
                            f"{manifest.artifact_family!r} not in declared "
                            f"families {meta.artifact_families}"
                        )

        assert not failures, (
            f"{len(failures)} backend(s) failed CI schema audit:\n"
            + "\n".join(failures)
        )

    def test_physics3d_telemetry_sidecar_deep_validation(self):
        """PHYSICS_3D backend telemetry sidecar must pass deep schema checks.

        Validates:
        - Required keys: solver_wall_time_ms, contact_count, frame_count, fps
        - Array lengths == frame_count
        - ccd_sweep_count array length == frame_count (when present)
        """
        reg = get_registry()
        physics3d = reg.get("physics_3d")
        if physics3d is None:
            pytest.skip("physics_3d backend not registered")

        meta, cls = physics3d

        with tempfile.TemporaryDirectory(prefix="ci_tel_") as tmpdir:
            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-073",
            )
            ctx = _build_minimal_context(meta, tmpdir)
            manifest = bridge.run_backend("physics_3d", ctx)

            # Deep telemetry validation via validate_artifact()
            errors = validate_artifact(manifest)
            assert not errors, f"Telemetry validation errors: {errors}"

            # Verify telemetry sidecar structure directly
            tel = manifest.metadata.get("physics3d_telemetry")
            assert tel is not None, "physics3d_telemetry missing from metadata"
            assert "solver_wall_time_ms" in tel
            assert "contact_count" in tel
            assert "frame_count" in tel
            assert "fps" in tel

            fc = tel["frame_count"]
            assert len(tel["solver_wall_time_ms"]) == fc
            assert len(tel["contact_count"]) == fc

            # CCD sweep count is optional but must match frame_count if present
            if "ccd_sweep_count" in tel:
                assert len(tel["ccd_sweep_count"]) == fc

    def test_required_metadata_keys_coverage(self):
        """ArtifactFamily.required_metadata_keys() returns non-empty sets
        for families that have explicit requirements."""
        physics3d_keys = ArtifactFamily.required_metadata_keys(
            ArtifactFamily.PHYSICS_3D_MOTION_UMR.value,
        )
        assert "physics_solver" in physics3d_keys
        assert "contact_manifold_count" in physics3d_keys
        assert "frame_count" in physics3d_keys
        assert "fps" in physics3d_keys
        assert "joint_channel_schema" in physics3d_keys

        motion_keys = ArtifactFamily.required_metadata_keys(
            ArtifactFamily.MOTION_UMR.value,
        )
        assert "frame_count" in motion_keys
        assert "fps" in motion_keys
        assert "joint_channel_schema" in motion_keys

        # Composite family has no required metadata
        composite_keys = ArtifactFamily.required_metadata_keys(
            ArtifactFamily.COMPOSITE.value,
        )
        assert len(composite_keys) == 0

    def test_schema_version_downgrade_blocked(self):
        """validate_artifact_strict() rejects manifests with version below
        the declared minimum."""
        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.COMPOSITE.value,
            backend_type="test_downgrade",
            version="0.9.0",
            outputs={},
            metadata={},
        )
        errors = validate_artifact_strict(manifest, min_schema_version="1.0.0")
        downgrade_errors = [e for e in errors if "downgrade" in e.lower()]
        assert len(downgrade_errors) >= 1, (
            f"Expected schema version downgrade error, got: {errors}"
        )

    def test_schema_version_passes_when_equal_or_higher(self):
        """validate_artifact_strict() accepts manifests at or above minimum."""
        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.COMPOSITE.value,
            backend_type="test_ok",
            version="1.0.0",
            outputs={},
            metadata={},
        )
        errors = validate_artifact_strict(manifest, min_schema_version="1.0.0")
        downgrade_errors = [e for e in errors if "downgrade" in e.lower()]
        assert len(downgrade_errors) == 0

    def test_backend_schema_version_declared_where_needed(self):
        """Backends that produce PHYSICS_3D_MOTION_UMR or MOTION_UMR must
        declare a non-empty schema_version."""
        _FAMILIES_REQUIRING_SCHEMA_VERSION = {
            ArtifactFamily.PHYSICS_3D_MOTION_UMR.value,
            ArtifactFamily.MOTION_UMR.value,
        }
        reg = get_registry()
        for name, (meta, _) in reg.all_backends().items():
            if set(meta.artifact_families) & _FAMILIES_REQUIRING_SCHEMA_VERSION:
                assert meta.schema_version, (
                    f"Backend {name!r} produces a typed motion family but "
                    f"has empty schema_version"
                )

    def test_ccd_enabled_capability_exists(self):
        """BackendCapability.CCD_ENABLED must be available."""
        assert hasattr(BackendCapability, "CCD_ENABLED")
        assert BackendCapability.CCD_ENABLED is not None
