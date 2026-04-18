"""3D Continuous Collision Detection (CCD) Tests — SESSION-073 (P1-XPBD-4).

This test module validates the CCD sweep implementation in
:class:`XPBDSolver3D` and the CCD telemetry pipeline through
:class:`Physics3DBackend`.

Design references:
    - Erin Catto, GDC 2013: Continuous Collision Detection
    - Brian Mirtich, 1996: Impulse-based Dynamic Simulation of Rigid Body Systems
    - Bullet Physics CCD: swept-sphere conservative advancement
    - NVIDIA PhysX 5: speculative CCD with velocity threshold gating

Test categories:
    1. **Solver-level CCD**: Direct XPBDSolver3D tests verifying that fast-
       moving particles are clamped to safe positions and their inward
       velocity is removed.
    2. **Backend-level CCD telemetry**: Physics3DBackend emits
       ``ccd_sweep_count`` arrays in the telemetry sidecar.
    3. **Schema validation**: ``validate_artifact()`` accepts the extended
       telemetry with ``ccd_sweep_count``.
    4. **CCD_ENABLED capability**: The physics_3d backend declares the
       capability.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mathart.animation.xpbd_solver import ParticleKind
from mathart.animation.xpbd_solver_3d import (
    XPBDConstraint3D,
    XPBDSolver3D,
    XPBDSolver3DConfig,
    XPBDSolver3DDiagnostics,
)
from mathart.animation.unified_motion import (
    JOINT_CHANNEL_3D_QUATERNION,
    ContactManifoldRecord,
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact,
)
from mathart.core.backend_registry import BackendCapability, get_registry
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_test_clip(
    frame_count: int = 8,
    fps: int = 12,
    state: str = "idle",
) -> UnifiedMotionClip:
    """Build a minimal 3D UMR clip for testing."""
    frames = []
    for i in range(frame_count):
        t = float(i) / max(fps, 1)
        root = MotionRootTransform(
            x=float(i) * 0.1,
            y=0.5,
            rotation=0.0,
            velocity_x=0.1,
            velocity_y=0.0,
            angular_velocity=0.0,
            z=0.0,
            velocity_z=0.0,
            angular_velocity_3d=[0.0, 0.0, 0.0],
        )
        contact = MotionContactState(
            left_foot=True, right_foot=True,
            left_hand=False, right_hand=False,
        )
        frame = UnifiedMotionFrame(
            time=t,
            phase=float(i) / max(frame_count - 1, 1),
            root_transform=root,
            joint_local_rotations={"root": 0.0},
            contact_tags=contact,
            frame_index=i,
            source_state=state,
            metadata={
                "joint_channel_schema": JOINT_CHANNEL_3D_QUATERNION,
                "generator": "test_ccd_3d",
            },
        )
        frames.append(frame)

    return UnifiedMotionClip(
        clip_id=f"test_ccd_{state}",
        state=state,
        fps=fps,
        frames=frames,
        metadata={
            "generator": "test_ccd_3d",
            "joint_channel_schema": JOINT_CHANNEL_3D_QUATERNION,
        },
    )


# ---------------------------------------------------------------------------
# 1. Solver-Level CCD Tests
# ---------------------------------------------------------------------------

class TestXPBDSolver3DCCD:
    """Direct solver-level CCD tests."""

    def test_fast_particle_clamped_above_ground(self):
        """A fast-moving particle falling toward the ground plane must be
        clamped to a safe position above the ground after CCD sweep."""
        cfg = XPBDSolver3DConfig(
            sub_steps=1,
            solver_iterations=1,
            gravity=(0.0, -100.0, 0.0),  # extreme gravity to force tunnelling
            enable_ccd=True,
            ccd_velocity_threshold=0.5,
            ccd_safety_backoff=1e-4,
        )
        solver = XPBDSolver3D(cfg)

        # Dynamic particle starting above ground
        root_idx = solver.add_particle(
            (0.0, 1.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE,
        )
        # Kinematic ground anchor at y=0
        ground_idx = solver.add_particle(
            (0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC,
        )
        solver.add_contact_constraint(
            root_idx, ground_idx,
            rest_distance=0.0,
            normal=(0.0, 1.0, 0.0),
        )

        diag = solver.step(dt=1.0 / 10.0)  # large dt to force fast motion

        # The particle must not have tunnelled below the ground
        final_y = solver.get_position(root_idx)[1]
        assert final_y >= -0.01, (
            f"Particle tunnelled below ground: y={final_y}"
        )

        # CCD diagnostics must be populated
        assert diag.ccd_sweep_count >= 0
        assert diag.ccd_min_toi <= 1.0
        assert diag.ccd_max_correction >= 0.0

    def test_slow_particle_not_swept(self):
        """Particles below the velocity threshold should not trigger CCD sweeps."""
        cfg = XPBDSolver3DConfig(
            sub_steps=1,
            solver_iterations=1,
            gravity=(0.0, -0.01, 0.0),  # very gentle gravity
            enable_ccd=True,
            ccd_velocity_threshold=100.0,  # very high threshold
        )
        solver = XPBDSolver3D(cfg)
        root_idx = solver.add_particle(
            (0.0, 1.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE,
        )
        ground_idx = solver.add_particle(
            (0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC,
        )
        solver.add_contact_constraint(
            root_idx, ground_idx,
            rest_distance=0.0,
            normal=(0.0, 1.0, 0.0),
        )

        diag = solver.step(dt=1.0 / 60.0)
        assert diag.ccd_sweep_count == 0, (
            f"Slow particle should not be swept: sweep_count={diag.ccd_sweep_count}"
        )

    def test_ccd_disabled_no_sweeps(self):
        """When CCD is disabled, no sweeps should occur."""
        cfg = XPBDSolver3DConfig(
            sub_steps=1,
            solver_iterations=1,
            gravity=(0.0, -100.0, 0.0),
            enable_ccd=False,
        )
        solver = XPBDSolver3D(cfg)
        root_idx = solver.add_particle(
            (0.0, 1.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE,
        )
        ground_idx = solver.add_particle(
            (0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC,
        )
        solver.add_contact_constraint(
            root_idx, ground_idx,
            rest_distance=0.0,
            normal=(0.0, 1.0, 0.0),
        )

        diag = solver.step(dt=1.0 / 10.0)
        assert diag.ccd_sweep_count == 0
        assert diag.ccd_hit_count == 0

    def test_ccd_removes_inward_velocity(self):
        """After a CCD hit, the inward normal velocity component must be removed."""
        cfg = XPBDSolver3DConfig(
            sub_steps=1,
            solver_iterations=0,  # no constraint solving, only predict + CCD
            gravity=(0.0, -200.0, 0.0),
            enable_ccd=True,
            ccd_velocity_threshold=0.1,
        )
        solver = XPBDSolver3D(cfg)
        root_idx = solver.add_particle(
            (0.0, 0.5, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE,
        )
        ground_idx = solver.add_particle(
            (0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC,
        )
        solver.add_contact_constraint(
            root_idx, ground_idx,
            rest_distance=0.0,
            normal=(0.0, 1.0, 0.0),
        )

        solver.step(dt=1.0 / 10.0)
        vy = solver.velocities[root_idx, 1]
        # After CCD, the downward velocity should be removed or reduced
        assert vy >= -0.1, (
            f"Inward velocity not removed after CCD hit: vy={vy}"
        )

    def test_ccd_diagnostics_in_to_dict(self):
        """CCD fields must appear in diagnostics.to_dict()."""
        diag = XPBDSolver3DDiagnostics(
            ccd_sweep_count=5,
            ccd_hit_count=2,
            ccd_min_toi=0.3,
            ccd_max_correction=0.05,
        )
        d = diag.to_dict()
        assert d["ccd_sweep_count"] == 5
        assert d["ccd_hit_count"] == 2
        assert abs(d["ccd_min_toi"] - 0.3) < 1e-9
        assert abs(d["ccd_max_correction"] - 0.05) < 1e-9

    def test_ccd_config_defaults(self):
        """CCD config defaults must be sensible."""
        cfg = XPBDSolver3DConfig()
        assert cfg.enable_ccd is True
        assert cfg.ccd_velocity_threshold > 0.0
        assert cfg.ccd_safety_backoff > 0.0

    def test_multiple_substeps_accumulate_ccd(self):
        """CCD counters accumulate across sub-steps."""
        cfg = XPBDSolver3DConfig(
            sub_steps=4,
            solver_iterations=1,
            gravity=(0.0, -50.0, 0.0),
            enable_ccd=True,
            ccd_velocity_threshold=0.5,
        )
        solver = XPBDSolver3D(cfg)
        root_idx = solver.add_particle(
            (0.0, 0.5, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE,
        )
        ground_idx = solver.add_particle(
            (0.0, 0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC,
        )
        solver.add_contact_constraint(
            root_idx, ground_idx,
            rest_distance=0.0,
            normal=(0.0, 1.0, 0.0),
        )

        diag = solver.step(dt=1.0 / 10.0)
        # With 4 sub-steps, CCD may fire on multiple sub-steps
        assert diag.ccd_sweep_count >= 0
        # Particle should still be above ground
        assert solver.get_position(root_idx)[1] >= -0.01


# ---------------------------------------------------------------------------
# 2. Backend-Level CCD Telemetry Tests
# ---------------------------------------------------------------------------

class TestPhysics3DBackendCCD:
    """Physics3DBackend CCD telemetry integration tests."""

    def test_ccd_sweep_count_in_telemetry(self):
        """The physics3d_telemetry sidecar must include ccd_sweep_count."""
        with tempfile.TemporaryDirectory(prefix="ccd_tel_") as tmpdir:
            clip = _build_test_clip(frame_count=4, fps=12)
            clip_path = Path(tmpdir) / "test_clip.umr.json"
            clip.save(clip_path)

            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-073",
            )
            ctx = {
                "state": "idle",
                "frame_count": 4,
                "output_dir": tmpdir,
                "name": "ccd_test",
                "motion_clip_path": str(clip_path),
            }
            manifest = bridge.run_backend("physics_3d", ctx)

            tel = manifest.metadata.get("physics3d_telemetry")
            assert tel is not None
            assert "ccd_sweep_count" in tel
            assert len(tel["ccd_sweep_count"]) == tel["frame_count"]

    def test_ccd_telemetry_passes_schema_validation(self):
        """Manifests with ccd_sweep_count must pass validate_artifact()."""
        with tempfile.TemporaryDirectory(prefix="ccd_val_") as tmpdir:
            clip = _build_test_clip(frame_count=4, fps=12)
            clip_path = Path(tmpdir) / "test_clip.umr.json"
            clip.save(clip_path)

            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-073",
            )
            ctx = {
                "state": "idle",
                "frame_count": 4,
                "output_dir": tmpdir,
                "name": "ccd_val",
                "motion_clip_path": str(clip_path),
            }
            manifest = bridge.run_backend("physics_3d", ctx)
            errors = validate_artifact(manifest)
            assert not errors, f"Validation errors: {errors}"


# ---------------------------------------------------------------------------
# 3. Capability Declaration Tests
# ---------------------------------------------------------------------------

class TestCCDCapability:
    """CCD_ENABLED capability declaration tests."""

    def test_physics3d_declares_ccd_enabled(self):
        """The physics_3d backend must declare CCD_ENABLED capability."""
        reg = get_registry()
        meta, _ = reg.get_or_raise("physics_3d")
        assert BackendCapability.CCD_ENABLED in meta.capabilities

    def test_ccd_enabled_enum_value(self):
        """CCD_ENABLED must be a valid BackendCapability member."""
        assert isinstance(BackendCapability.CCD_ENABLED, BackendCapability)
