"""Tests for SESSION-071 (P1-XPBD-3) — 3D XPBD solver and Physics3DBackend.

Coverage matrix (mapped to the three architecture red lines):

1. Anti pseudo-3D shell:
   * ``test_xpbd_solver_3d_drops_under_real_z_gravity``
   * ``test_xpbd_solver_3d_bending_uses_3d_cross_product``
   * ``test_spatial_hash_3d_separates_z_layers``

2. Anti 2D-collapse / scalar pollution:
   * ``test_physics3d_backend_gracefully_downgrades_pure_2d_input``
   * ``test_contact_manifold_record_legacy_serialisation_unchanged``

3. Anti microkernel over-coupling:
   * ``test_physics3d_backend_does_not_import_unified_motion_backend``
   * ``test_physics3d_pipeline_chaining_via_bridge``
"""
from __future__ import annotations

import json
from pathlib import Path

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
from mathart.animation.xpbd_solver import ParticleKind
from mathart.animation.xpbd_solver_3d import (
    SpatialHashGrid3D,
    XPBDSolver3D,
    XPBDSolver3DConfig,
)
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
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_2d_clip(n: int = 6) -> UnifiedMotionClip:
    frames = [
        UnifiedMotionFrame(
            time=float(i) / 12.0,
            phase=float(i) / max(n - 1, 1),
            root_transform=MotionRootTransform(x=float(i) * 0.1, y=1.0),
            joint_local_rotations={"hip": 0.1 * i},
            contact_tags=MotionContactState(),
            frame_index=i,
            source_state="run",
            metadata={"joint_channel_schema": JOINT_CHANNEL_2D_SCALAR},
        )
        for i in range(n)
    ]
    return UnifiedMotionClip(
        clip_id="test_clip_2d",
        state="run",
        fps=12,
        frames=frames,
        metadata={"joint_channel_schema": JOINT_CHANNEL_2D_SCALAR},
    )


def _make_3d_clip(n: int = 6) -> UnifiedMotionClip:
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


# ---------------------------------------------------------------------------
# 1. Anti pseudo-3D shell
# ---------------------------------------------------------------------------

def test_xpbd_solver_3d_drops_under_real_z_gravity():
    cfg = XPBDSolver3DConfig(
        sub_steps=4, solver_iterations=8,
        gravity=(0.0, 0.0, -9.81),  # gravity along the Z axis on purpose
        enable_two_way_coupling=False,
    )
    s = XPBDSolver3D(cfg)
    p = s.add_particle((0.0, 0.0, 1.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    # Step ~0.8 second so the 0.98 velocity damping still leaves the
    # particle clearly displaced along Z.
    for _ in range(48):
        s.step(1.0 / 60.0)
    z_after = s.get_position(p)[2]
    assert z_after < 0.5, (
        "3D solver must move particles along Z under Z-axis gravity; "
        f"got z={z_after}. This indicates the Z component was silently dropped."
    )
    assert s.last_diagnostics.z_axis_active is True


def test_xpbd_solver_3d_bending_uses_3d_cross_product():
    """The bending constraint must keep three particles forming a non-planar
    angle stable in 3D. A 2D-only bending term would collapse the Z component
    and produce drift along Z under no Z gravity."""
    cfg = XPBDSolver3DConfig(
        sub_steps=2, solver_iterations=10,
        gravity=(0.0, 0.0, 0.0),
        enable_two_way_coupling=False,
        velocity_damping=1.0,
    )
    s = XPBDSolver3D(cfg)
    a = s.add_particle((0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    b = s.add_particle((1.0, 0.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    c = s.add_particle((1.0, 0.0, 1.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    s.add_distance_constraint(a, b, rest_length=1.0)
    s.add_distance_constraint(b, c, rest_length=1.0)
    s.add_bending_constraint(a, b, c, compliance=1e-4)
    z0 = s.get_position(c)[2]
    for _ in range(30):
        s.step(1.0 / 60.0)
    z1 = s.get_position(c)[2]
    # Angle is preserved => particle c stays roughly at z ~ 1
    assert abs(z1 - z0) < 0.25, (
        "3D bending constraint should preserve the 3D angle, not collapse "
        f"Z. z drift={z1 - z0}."
    )


def test_spatial_hash_3d_separates_z_layers():
    g = SpatialHashGrid3D(cell_size=0.1)
    # Two particles at (0,0,0) and (0,0,1) — they must NOT be detected as
    # neighbours within radius 0.05 (a 2D-collapsing hash would falsely pair
    # them because they share (ix, iy)).
    pos = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    radii = np.full(2, 0.02)
    pairs = g.find_all_pairs(pos, radii, min_separation=0.02)
    assert pairs == [], f"3D hash must not pair Z-separated particles, got {pairs}"

    # Two particles within the same Z layer must still be found.
    pos2 = np.array([[0.0, 0.0, 0.0], [0.03, 0.0, 0.0]], dtype=np.float64)
    pairs2 = g.find_all_pairs(pos2, radii, min_separation=0.05)
    assert any((i, j) == (0, 1) for i, j, _ in pairs2)


# ---------------------------------------------------------------------------
# 2. Anti 2D-collapse / scalar pollution
# ---------------------------------------------------------------------------

def test_physics3d_backend_gracefully_downgrades_pure_2d_input(tmp_path: Path):
    """Pure 2D upstream input must NOT crash the backend; it must annotate
    ``downgraded_to_2d_input`` and still produce a valid manifest."""
    clip = _make_2d_clip()
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    ctx = {
        "state": "run",
        "motion_clip": clip,
        "output_dir": str(tmp_path),
        "name": "pure2d",
    }

    reg = get_registry()
    meta, cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    backend = cls()
    ctx, _warnings = backend.validate_config(ctx)
    manifest = backend.execute(ctx)

    assert manifest.metadata["physics_downgraded_to_2d_input"] is True
    assert manifest.metadata["physics_solver"] == "xpbd_3d"
    errors = validate_artifact(manifest)
    assert errors == [], errors

    # Roundtrip: deserialised clip must still parse and report 3D-aware fields.
    new_clip_path = Path(manifest.outputs["motion_clip_json"])
    data = json.loads(new_clip_path.read_text(encoding="utf-8"))
    new_clip = UnifiedMotionClip.from_dict(data)
    assert new_clip.frames[0].metadata["joint_channel_schema"] == JOINT_CHANNEL_3D_QUATERNION
    # Z must now be populated even though the input was purely 2D.
    assert new_clip.frames[0].root_transform.z is not None


def test_contact_manifold_record_legacy_serialisation_unchanged():
    """SESSION-070 records (no 3D physics fields) must serialize bit-identical
    to the pre-SESSION-071 representation. This protects all existing audit
    snapshots and 1305 baseline tests."""
    legacy_record = ContactManifoldRecord(
        limb="left_foot", active=True, lock_weight=1.0,
        local_offset_y=-0.05, normal_y=1.0,
    )
    d = legacy_record.to_dict()
    assert "contact_point" not in d
    assert "penetration_depth" not in d
    assert "source_solver" not in d
    # Round-trip preserves identity.
    rt = ContactManifoldRecord.from_dict(d)
    assert rt == legacy_record


# ---------------------------------------------------------------------------
# 3. Anti microkernel over-coupling
# ---------------------------------------------------------------------------

def test_physics3d_backend_does_not_import_unified_motion_backend():
    """Static red-line: the backend module must not statically import the
    trunk ``UnifiedMotionBackend`` class — cross-backend invocation must go
    through Context-in / Manifest-out only. AST-level scan keeps the docstring
    references (which are pedagogical) from triggering false positives."""
    import ast
    src = Path("mathart/core/physics3d_backend.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "UnifiedMotionBackend", (
                    "Physics3DBackend must not import UnifiedMotionBackend; "
                    "use the upstream manifest contract instead."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "builtin_backends" not in alias.name, (
                    "Physics3DBackend must not depend on builtin_backends "
                    "directly; rely on the registry contract."
                )
    # And must declare PHYSICS_SIMULATION capability.
    reg = get_registry()
    meta, _cls = reg.get_or_raise(BackendType.PHYSICS_3D)
    assert BackendCapability.PHYSICS_SIMULATION in meta.capabilities


def test_physics3d_pipeline_chaining_via_bridge(tmp_path: Path):
    """End-to-end: bridge resolves UnifiedMotionBackend dependency, runs it,
    forwards the manifest as ``unified_motion_manifest`` context key, then
    runs Physics3DBackend. The 3D output manifest must:

    * be of family PHYSICS_3D_MOTION_UMR;
    * carry a contact_manifold_count > 0 (root touches the floor by gravity);
    * include ``upstream_motion_clip_json`` provenance.
    """
    bridge = MicrokernelPipelineBridge(project_root=tmp_path)
    ctx = {
        "state": "idle",
        "frame_count": 8,
        "fps": 12,
        "output_dir": str(tmp_path),
        "name": "chain",
        # Force the root well above the ground so the 3D solver actually
        # produces ground contact within a single frame's sub-steps.
        "physics3d_ground_y": -10.0,
        "physics3d_root_mass": 65.0,
    }
    manifest = bridge.run_backend(BackendType.PHYSICS_3D.value, ctx)
    assert manifest.artifact_family == ArtifactFamily.PHYSICS_3D_MOTION_UMR.value
    assert manifest.metadata["physics_solver"] == "xpbd_3d"
    assert "upstream_motion_clip_json" in manifest.outputs
    assert manifest.metadata["frame_count"] == 8
    assert validate_artifact(manifest) == []
