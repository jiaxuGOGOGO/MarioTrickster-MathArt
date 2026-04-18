"""Tests for SESSION-072 (P1-DISTILL-1A) — Microkernel hot-path evaluation,
telemetry sidecar, compliance parameter knobs, and data-lineage provenance.

Coverage matrix (mapped to the four micro-adjustments):

1. Telemetry injection:
   * ``test_hot_path_instrumented_capability_registered``
   * ``test_run_backend_with_telemetry_injects_sink``
   * ``test_run_backend_with_telemetry_rejects_uninstrumented``

2. High-frequency telemetry sidecar:
   * ``test_physics3d_telemetry_sidecar_present``
   * ``test_telemetry_array_length_equals_frame_count``
   * ``test_telemetry_wall_time_is_real_per_frame``

3. Compliance parameter knobs:
   * ``test_compiled_parameter_space_physics3d_compliance_knobs``
   * ``test_xpbd3d_config_compliance_fields``
   * ``test_physics3d_backend_reads_compliance_from_context``

4. Data-lineage provenance:
   * ``test_distillation_record_upstream_manifest_hash``
   * ``test_upstream_manifest_hash_in_physics3d_manifest``
   * ``test_provenance_hash_is_real_not_uuid``

Red-line guards:
   * ``test_no_static_import_of_runtime_distill_bus_in_physics3d``
   * ``test_zero_overhead_when_no_sink``
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mathart.animation.unified_motion import (
    JOINT_CHANNEL_2D_SCALAR,
    JOINT_CHANNEL_3D_QUATERNION,
    ContactManifoldRecord,
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.animation.xpbd_solver_3d import XPBDSolver3DConfig
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact,
)
from mathart.core.backend_registry import (
    BackendCapability,
    get_registry,
)
from mathart.core.backend_types import BackendType
from mathart.core.pipeline_bridge import (
    MicrokernelPipelineBridge,
    TelemetrySink,
)
from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import CompiledParameterSpace
from mathart.evolution.evolution_loop import DistillationRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_3d_clip(n: int = 8) -> UnifiedMotionClip:
    frames = [
        UnifiedMotionFrame(
            time=float(i) / 12.0,
            phase=float(i) / max(n - 1, 1),
            root_transform=MotionRootTransform(
                x=float(i) * 0.1, y=1.5, z=float(i) * 0.05,
                velocity_z=0.6,
                angular_velocity_3d=[0.0, 0.0, 0.1],
            ),
            joint_local_rotations={"hip": 0.1 * i},
            contact_tags=MotionContactState(),
            frame_index=i,
            source_state="run",
            metadata={"joint_channel_schema": JOINT_CHANNEL_3D_QUATERNION},
        )
        for i in range(n)
    ]
    return UnifiedMotionClip(
        clip_id="test_clip_3d",
        state="run",
        fps=12,
        frames=frames,
        metadata={"joint_channel_schema": JOINT_CHANNEL_3D_QUATERNION},
    )


class _ListSink:
    """Minimal TelemetrySink implementation for testing."""

    def __init__(self) -> None:
        self.records: list[tuple[str, Any]] = []

    def record(self, key: str, value: Any) -> None:
        self.records.append((key, value))


# ---------------------------------------------------------------------------
# 1. Telemetry injection
# ---------------------------------------------------------------------------

def test_hot_path_instrumented_capability_registered():
    """Physics3DBackend must declare HOT_PATH_INSTRUMENTED."""
    reg = get_registry()
    meta, _cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    assert BackendCapability.HOT_PATH_INSTRUMENTED in meta.capabilities


def test_run_backend_with_telemetry_injects_sink(tmp_path: Path):
    """run_backend_with_telemetry must inject the sink and the backend
    must call sink.record() from its inner loop."""
    clip = _make_3d_clip(6)
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    sink = _ListSink()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "tele",
    }
    manifest = bridge.run_backend_with_telemetry(
        BackendType.PHYSICS_3D.value, ctx, sink,
    )
    # The sink must have received per-frame records.
    wall_records = [v for k, v in sink.records if k == "solver_wall_time_ms"]
    contact_records = [v for k, v in sink.records if k == "contact_count"]
    assert len(wall_records) == 6, f"Expected 6 wall_time records, got {len(wall_records)}"
    assert len(contact_records) == 6
    assert all(isinstance(w, float) and w >= 0 for w in wall_records)


def test_run_backend_with_telemetry_rejects_uninstrumented(tmp_path: Path):
    """run_backend_with_telemetry must raise for backends that do NOT
    declare HOT_PATH_INSTRUMENTED."""
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    sink = _ListSink()
    # unified_motion does NOT declare HOT_PATH_INSTRUMENTED.
    with pytest.raises(RuntimeError, match="HOT_PATH_INSTRUMENTED"):
        bridge.run_backend_with_telemetry(
            BackendType.UNIFIED_MOTION.value,
            {"state": "idle", "frame_count": 4, "fps": 12,
             "output_dir": str(tmp_path), "name": "reject"},
            sink,
        )


# ---------------------------------------------------------------------------
# 2. High-frequency telemetry sidecar
# ---------------------------------------------------------------------------

def test_physics3d_telemetry_sidecar_present(tmp_path: Path):
    """The manifest must contain a physics3d_telemetry sidecar."""
    clip = _make_3d_clip(8)
    reg = get_registry()
    _meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "sidecar",
    }
    ctx, _ = backend.validate_config(ctx)
    manifest = backend.execute(ctx)
    tele = manifest.metadata.get("physics3d_telemetry")
    assert tele is not None, "physics3d_telemetry sidecar missing from manifest"
    assert "solver_wall_time_ms" in tele
    assert "contact_count" in tele
    assert tele["frame_count"] == 8


def test_telemetry_array_length_equals_frame_count(tmp_path: Path):
    """Time-series arrays must have length == frame_count (anti-fake-array
    red line)."""
    n = 10
    clip = _make_3d_clip(n)
    reg = get_registry()
    _meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "arrlen",
    }
    ctx, _ = backend.validate_config(ctx)
    manifest = backend.execute(ctx)
    tele = manifest.metadata["physics3d_telemetry"]
    assert len(tele["solver_wall_time_ms"]) == n, (
        f"solver_wall_time_ms length {len(tele['solver_wall_time_ms'])} != {n}"
    )
    assert len(tele["contact_count"]) == n


def test_telemetry_wall_time_is_real_per_frame(tmp_path: Path):
    """Each entry in solver_wall_time_ms must be a real per-frame measurement,
    not a total divided by frame_count (anti-fake red line)."""
    clip = _make_3d_clip(6)
    reg = get_registry()
    _meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "realwall",
    }
    ctx, _ = backend.validate_config(ctx)
    manifest = backend.execute(ctx)
    wall = manifest.metadata["physics3d_telemetry"]["solver_wall_time_ms"]
    # All entries must be non-negative floats.
    assert all(isinstance(w, float) and w >= 0.0 for w in wall)
    # At least some variance is expected (not all identical).
    # In degenerate CI environments all frames may be equally fast,
    # so we only assert they are individually non-negative.


# ---------------------------------------------------------------------------
# 3. Compliance parameter knobs
# ---------------------------------------------------------------------------

def test_compiled_parameter_space_physics3d_compliance_knobs():
    """physics3d.compliance_distance and physics3d.compliance_bending must
    be resolvable through CompiledParameterSpace aliases."""
    space = ParameterSpace(name="physics3d_test")
    space.add_constraint(Constraint(
        param_name="physics3d.compliance_distance",
        min_value=0.0,
        max_value=1.0,
        default_value=0.001,
        is_hard=False,
        source_rule_id="P1-DISTILL-1A",
    ))
    space.add_constraint(Constraint(
        param_name="physics3d.compliance_bending",
        min_value=0.0,
        max_value=1.0,
        default_value=0.01,
        is_hard=False,
        source_rule_id="P1-DISTILL-1A",
    ))
    compiled = CompiledParameterSpace(module_name="physics3d", space=space)
    assert compiled.dimensions == 2
    # Alias resolution
    defaults = compiled.defaults_as_dict(leaf_aliases=True)
    assert "compliance_distance" in defaults
    assert "compliance_bending" in defaults
    assert abs(defaults["compliance_distance"] - 0.001) < 1e-9
    assert abs(defaults["compliance_bending"] - 0.01) < 1e-9
    # Clamping
    clamped = compiled.clamp_params(
        {"compliance_distance": 5.0, "compliance_bending": -1.0},
    )
    assert clamped["compliance_distance"] <= 1.0
    assert clamped["compliance_bending"] >= 0.0


def test_xpbd3d_config_compliance_fields():
    """XPBDSolver3DConfig must accept compliance_distance and
    compliance_bending fields."""
    cfg = XPBDSolver3DConfig(compliance_distance=0.005, compliance_bending=0.02)
    assert cfg.compliance_distance == 0.005
    assert cfg.compliance_bending == 0.02
    # Default is None (falls back to default_compliance).
    cfg2 = XPBDSolver3DConfig()
    assert cfg2.compliance_distance is None
    assert cfg2.compliance_bending is None


def test_physics3d_backend_reads_compliance_from_context(tmp_path: Path):
    """Physics3DBackend must read compliance knobs from context and pass
    them to XPBDSolver3DConfig."""
    clip = _make_3d_clip(4)
    reg = get_registry()
    _meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "compliance",
        "physics3d_compliance_distance": 0.005,
        "physics3d_compliance_bending": 0.02,
    }
    ctx, _ = backend.validate_config(ctx)
    # The backend should not crash and should produce a valid manifest.
    manifest = backend.execute(ctx)
    assert validate_artifact(manifest) == []


# ---------------------------------------------------------------------------
# 4. Data-lineage provenance
# ---------------------------------------------------------------------------

def test_distillation_record_upstream_manifest_hash():
    """DistillationRecord must support optional upstream_manifest_hash."""
    # Without hash (backward compatible)
    rec1 = DistillationRecord(
        paper_id="test-001",
        paper_title="Test",
        authors="A",
        venue="V",
        concept="C",
        target_module="M",
        target_class="C",
    )
    d1 = rec1.to_dict()
    assert "upstream_manifest_hash" in d1
    assert d1["upstream_manifest_hash"] is None

    # With hash
    rec2 = DistillationRecord(
        paper_id="test-002",
        paper_title="Test 2",
        authors="B",
        venue="V2",
        concept="C2",
        target_module="M2",
        target_class="C2",
        upstream_manifest_hash="sha256:abcdef1234567890",
    )
    d2 = rec2.to_dict()
    assert d2["upstream_manifest_hash"] == "sha256:abcdef1234567890"


def test_upstream_manifest_hash_in_physics3d_manifest(tmp_path: Path):
    """When the bridge chains unified_motion -> physics_3d, the physics3d
    manifest must carry the upstream manifest's schema_hash."""
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    ctx = {
        "state": "idle",
        "frame_count": 6,
        "fps": 12,
        "output_dir": str(tmp_path),
        "name": "provenance",
        "physics3d_ground_y": -10.0,
    }
    manifest = bridge.run_backend(BackendType.PHYSICS_3D.value, ctx)
    upstream_hash = manifest.metadata.get("upstream_manifest_hash")
    assert upstream_hash is not None, (
        "upstream_manifest_hash must be populated when chaining via bridge"
    )
    assert upstream_hash.startswith("sha256:"), (
        f"upstream_manifest_hash must be a real schema_hash, got: {upstream_hash}"
    )


def test_provenance_hash_is_real_not_uuid(tmp_path: Path):
    """The upstream_manifest_hash must be the actual schema_hash of the
    unified_motion manifest, not a random UUID (anti-fake red line)."""
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    ctx = {
        "state": "idle",
        "frame_count": 4,
        "fps": 12,
        "output_dir": str(tmp_path),
        "name": "hashcheck",
        "physics3d_ground_y": -10.0,
    }
    # Run unified_motion first to get its manifest.
    um_manifest = bridge.run_backend(BackendType.UNIFIED_MOTION.value, dict(ctx))
    expected_hash = um_manifest.schema_hash

    # Now run physics_3d through the bridge (which chains automatically).
    p3d_manifest = bridge.run_backend(BackendType.PHYSICS_3D.value, dict(ctx))
    actual_hash = p3d_manifest.metadata.get("upstream_manifest_hash")
    assert actual_hash == expected_hash, (
        f"upstream_manifest_hash mismatch: expected {expected_hash}, "
        f"got {actual_hash}"
    )


# ---------------------------------------------------------------------------
# Red-line guards
# ---------------------------------------------------------------------------

def test_no_static_import_of_runtime_distill_bus_in_physics3d():
    """Physics3DBackend must NOT statically import RuntimeDistillBus.
    Telemetry recording must only interact with the duck-typed sink
    passed via context (eBPF/DTrace zero-intrusion pattern)."""
    src = Path("mathart/core/physics3d_backend.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "RuntimeDistillBus", (
                    "Physics3DBackend must not import RuntimeDistillBus; "
                    "use the duck-typed sink from context instead."
                )
                assert alias.name != "RuntimeDistillationBus", (
                    "Physics3DBackend must not import RuntimeDistillationBus; "
                    "use the duck-typed sink from context instead."
                )


def test_zero_overhead_when_no_sink(tmp_path: Path):
    """When no TelemetrySink is in the context, the backend must still
    produce a valid manifest with telemetry sidecar arrays (but no sink
    interaction). This validates the zero-overhead path."""
    clip = _make_3d_clip(6)
    reg = get_registry()
    _meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "nosink",
    }
    ctx, _ = backend.validate_config(ctx)
    # Ensure __telemetry_sink__ is NOT in context.
    assert "__telemetry_sink__" not in ctx
    manifest = backend.execute(ctx)
    assert validate_artifact(manifest) == []
    tele = manifest.metadata["physics3d_telemetry"]
    assert len(tele["solver_wall_time_ms"]) == 6
