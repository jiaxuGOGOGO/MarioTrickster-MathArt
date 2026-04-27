"""SESSION-118 (P1-HUMAN-31C): Strict White-Box Tests for Pseudo-3D
Paper-Doll / Mesh-Shell DQS Backend.

This test module provides comprehensive coverage of:
1. Quaternion tensor utilities (batch multiply, conjugate, rotate)
2. Dual quaternion construction and extraction
3. Tensorized DQS skinning engine (volume preservation, antipodal
   correction, normalization guard, normal rotation)
4. Pseudo3DShellBackend registry discovery, validate_config, execute,
   and ArtifactManifest contract compliance
5. Cross-section area measurement for volume preservation verification
6. Integration with Mesh3D (SESSION-106 contract)

Architecture Discipline
-----------------------
- ✅ Per NEP-19: each test uses its own ``default_rng`` instance.
- ✅ Registry isolation: tests that reset the registry restore builtins.
- ✅ No global state: all test data is locally constructed.
- ✅ Strict tolerances: DQS mathematical invariants are verified to 1e-10.

Research Foundations
--------------------
[1] Kavan et al. (SIGGRAPH 2007) — "Skinning with Dual Quaternions"
[2] Arc System Works / Guilty Gear Xrd (GDC 2015) — 2.5D cel-shading
[3] Data-Oriented Tensor Skinning — industrial-grade einsum pipelines
"""
from __future__ import annotations

import math
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

# ── DQS Engine imports ────────────────────────────────────────────────────
from mathart.animation.dqs_engine import (
    quat_mul_batch,
    quat_conj_batch,
    quat_normalize_batch,
    quat_rotate_points_batch,
    quat_from_axis_angle_batch,
    dq_from_rotation_translation,
    dq_from_axis_angle_translation,
    dq_identity,
    dq_extract_translation,
    DQSSkinningResult,
    tensorized_dqs_skin,
    create_cylinder_mesh,
    compute_cylinder_skin_weights,
    compute_cross_section_area,
)

# ── Backend imports ───────────────────────────────────────────────────────
from mathart.core.backend_registry import get_registry
from mathart.core.artifact_schema import ArtifactManifest, ArtifactFamily
from mathart.core.backend_types import BackendType
from mathart.core import pseudo3d_shell_backend as _pseudo3d_shell_backend


@pytest.fixture(autouse=True)
def _ensure_pseudo3d_backend_registered():
    import importlib

    importlib.reload(_pseudo3d_shell_backend)


# ═══════════════════════════════════════════════════════════════════════════
#  Section 1: Quaternion Tensor Utilities
# ═══════════════════════════════════════════════════════════════════════════

class TestQuaternionBatchOps:
    """White-box tests for vectorized quaternion operations."""

    def test_quat_mul_identity(self):
        """Multiplying by identity quaternion returns the original."""
        rng = np.random.default_rng(42)
        q = quat_normalize_batch(rng.standard_normal((10, 4)))
        identity = np.array([1.0, 0.0, 0.0, 0.0])
        identity_batch = np.broadcast_to(identity, q.shape)
        result = quat_mul_batch(q, identity_batch)
        np.testing.assert_allclose(result, q, atol=1e-12)

    def test_quat_mul_inverse(self):
        """q * conj(q) = identity (for unit quaternions)."""
        rng = np.random.default_rng(43)
        q = quat_normalize_batch(rng.standard_normal((20, 4)))
        q_conj = quat_conj_batch(q)
        product = quat_mul_batch(q, q_conj)
        identity = np.zeros_like(product)
        identity[..., 0] = 1.0
        np.testing.assert_allclose(product, identity, atol=1e-12)

    def test_quat_mul_associativity(self):
        """(a * b) * c = a * (b * c) for quaternion multiplication."""
        rng = np.random.default_rng(44)
        a = quat_normalize_batch(rng.standard_normal((5, 4)))
        b = quat_normalize_batch(rng.standard_normal((5, 4)))
        c = quat_normalize_batch(rng.standard_normal((5, 4)))
        lhs = quat_mul_batch(quat_mul_batch(a, b), c)
        rhs = quat_mul_batch(a, quat_mul_batch(b, c))
        np.testing.assert_allclose(lhs, rhs, atol=1e-12)

    def test_quat_conj_involution(self):
        """conj(conj(q)) = q."""
        rng = np.random.default_rng(45)
        q = rng.standard_normal((15, 4))
        np.testing.assert_allclose(quat_conj_batch(quat_conj_batch(q)), q, atol=1e-15)

    def test_quat_normalize_unit_length(self):
        """Normalized quaternions have unit length."""
        rng = np.random.default_rng(46)
        q = rng.standard_normal((25, 4))
        normed = quat_normalize_batch(q)
        norms = np.linalg.norm(normed, axis=-1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-12)

    def test_quat_rotate_identity(self):
        """Rotation by identity quaternion preserves points."""
        pts = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        identity = np.array([[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]])
        result = quat_rotate_points_batch(identity, pts)
        np.testing.assert_allclose(result, pts, atol=1e-12)

    def test_quat_rotate_90_degrees_z(self):
        """90-degree rotation around Z axis: (1,0,0) -> (0,1,0)."""
        angle = np.pi / 2.0
        q = quat_from_axis_angle_batch(
            np.array([[0.0, 0.0, 1.0]]),
            np.array([angle]),
        )
        pts = np.array([[1.0, 0.0, 0.0]])
        result = quat_rotate_points_batch(q, pts)
        expected = np.array([[0.0, 1.0, 0.0]])
        np.testing.assert_allclose(result, expected, atol=1e-10)

    def test_quat_rotate_180_degrees_y(self):
        """180-degree rotation around Y axis: (1,0,0) -> (-1,0,0)."""
        angle = np.pi
        q = quat_from_axis_angle_batch(
            np.array([[0.0, 1.0, 0.0]]),
            np.array([angle]),
        )
        pts = np.array([[1.0, 0.0, 0.0]])
        result = quat_rotate_points_batch(q, pts)
        expected = np.array([[-1.0, 0.0, 0.0]])
        np.testing.assert_allclose(result, expected, atol=1e-10)

    def test_quat_rotate_preserves_length(self):
        """Rotation preserves vector length."""
        rng = np.random.default_rng(47)
        q = quat_normalize_batch(rng.standard_normal((50, 4)))
        pts = rng.standard_normal((50, 3))
        rotated = quat_rotate_points_batch(q, pts)
        original_norms = np.linalg.norm(pts, axis=-1)
        rotated_norms = np.linalg.norm(rotated, axis=-1)
        np.testing.assert_allclose(rotated_norms, original_norms, atol=1e-10)

    def test_quat_from_axis_angle_zero(self):
        """Zero angle produces identity quaternion."""
        axes = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        angles = np.array([0.0, 0.0])
        q = quat_from_axis_angle_batch(axes, angles)
        expected_w = np.array([1.0, 1.0])
        np.testing.assert_allclose(q[:, 0], expected_w, atol=1e-12)
        np.testing.assert_allclose(q[:, 1:], 0.0, atol=1e-12)


# ═══════════════════════════════════════════════════════════════════════════
#  Section 2: Dual Quaternion Construction & Extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestDualQuaternionConstruction:
    """White-box tests for dual quaternion construction and extraction."""

    def test_identity_dq(self):
        """Identity DQ has real=[1,0,0,0], dual=[0,0,0,0]."""
        dq = dq_identity()
        assert dq.shape == (8,)
        np.testing.assert_allclose(dq[:4], [1, 0, 0, 0], atol=1e-15)
        np.testing.assert_allclose(dq[4:], [0, 0, 0, 0], atol=1e-15)

    def test_identity_dq_batch(self):
        """Batch identity DQs."""
        dq = dq_identity((3, 2))
        assert dq.shape == (3, 2, 8)
        np.testing.assert_allclose(dq[..., 0], 1.0, atol=1e-15)
        np.testing.assert_allclose(dq[..., 1:], 0.0, atol=1e-15)

    def test_pure_translation_dq(self):
        """DQ from identity rotation + translation encodes translation."""
        t = np.array([3.0, 4.0, 5.0])
        r = np.array([1.0, 0.0, 0.0, 0.0])
        dq = dq_from_rotation_translation(r, t)
        extracted_t = dq_extract_translation(dq)
        np.testing.assert_allclose(extracted_t, t, atol=1e-10)

    def test_pure_rotation_dq(self):
        """DQ from rotation + zero translation has zero translation."""
        q = quat_from_axis_angle_batch(
            np.array([0.0, 0.0, 1.0]),
            np.array(np.pi / 4),
        )
        dq = dq_from_rotation_translation(q, np.array([0.0, 0.0, 0.0]))
        extracted_t = dq_extract_translation(dq)
        np.testing.assert_allclose(extracted_t, [0, 0, 0], atol=1e-10)

    def test_roundtrip_rotation_translation(self):
        """Build DQ from (R, t) and extract t back — roundtrip."""
        rng = np.random.default_rng(50)
        for _ in range(20):
            axis = rng.standard_normal(3)
            angle = rng.uniform(-np.pi, np.pi)
            t = rng.standard_normal(3) * 5.0
            q = quat_from_axis_angle_batch(
                axis[np.newaxis], np.array([angle]),
            )[0]
            dq = dq_from_rotation_translation(q, t)
            extracted_t = dq_extract_translation(dq)
            np.testing.assert_allclose(extracted_t, t, atol=1e-8)

    def test_dq_from_axis_angle_translation(self):
        """Convenience constructor matches manual construction."""
        axis = np.array([0.0, 1.0, 0.0])
        angle = np.array(np.pi / 3)
        t = np.array([1.0, 2.0, 3.0])
        dq1 = dq_from_axis_angle_translation(axis, angle, t)
        q = quat_from_axis_angle_batch(axis[np.newaxis], angle[np.newaxis])[0]
        dq2 = dq_from_rotation_translation(q, t)
        np.testing.assert_allclose(dq1, dq2, atol=1e-12)

    def test_unit_dq_constraint(self):
        """Unit DQ: ||real|| = 1 and dot(real, dual) = 0."""
        rng = np.random.default_rng(51)
        for _ in range(20):
            axis = rng.standard_normal(3)
            angle = rng.uniform(-np.pi, np.pi)
            t = rng.standard_normal(3) * 3.0
            q = quat_from_axis_angle_batch(
                axis[np.newaxis], np.array([angle]),
            )[0]
            dq = dq_from_rotation_translation(q, t)
            real = dq[:4]
            dual = dq[4:]
            # ||real|| = 1
            np.testing.assert_allclose(np.linalg.norm(real), 1.0, atol=1e-10)
            # dot(real, dual) = 0
            np.testing.assert_allclose(np.dot(real, dual), 0.0, atol=1e-10)


# ═══════════════════════════════════════════════════════════════════════════
#  Section 3: Tensorized DQS Skinning Engine
# ═══════════════════════════════════════════════════════════════════════════

class TestTensorizedDQSSkinning:
    """White-box tests for the core DQS skinning engine."""

    @staticmethod
    def _make_two_bone_cylinder(
        n_frames: int = 10,
        max_angle: float = np.pi / 2,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Create a standard two-bone cylinder test case.

        Returns (base_verts, base_normals, triangles, skin_weights, bone_dqs).
        """
        verts, normals, tris = create_cylinder_mesh(
            radius=0.5, height=2.0,
            radial_segments=32, height_segments=10,
            axis="y",
        )
        weights = compute_cylinder_skin_weights(
            verts, height=2.0, axis="y",
        )
        bone_dqs_list = []
        for f in range(n_frames):
            angle = (f / max(n_frames - 1, 1)) * max_angle
            dq0 = dq_identity()
            dq1 = dq_from_axis_angle_translation(
                np.array([0.0, 0.0, 1.0]),
                np.array(angle),
                np.array([0.0, 0.0, 0.0]),
            )
            bone_dqs_list.append(np.stack([dq0, dq1], axis=0))
        bone_dqs = np.stack(bone_dqs_list, axis=0)
        return verts, normals, tris, weights, bone_dqs

    def test_identity_skinning_preserves_mesh(self):
        """When all bones are identity, deformed mesh = base mesh."""
        verts, normals, tris = create_cylinder_mesh()
        weights = compute_cylinder_skin_weights(verts)
        V = verts.shape[0]
        B = 2
        bone_dqs = dq_identity((1, B))  # 1 frame, 2 bones, all identity
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        np.testing.assert_allclose(
            result.deformed_vertices[0], verts, atol=1e-10,
        )
        np.testing.assert_allclose(
            result.deformed_normals[0], normals, atol=1e-10,
        )

    def test_output_shapes(self):
        """Output shapes match (F, V, 3) for vertices and normals."""
        verts, normals, tris, weights, bone_dqs = self._make_two_bone_cylinder(
            n_frames=5,
        )
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        F, V = 5, verts.shape[0]
        assert result.deformed_vertices.shape == (F, V, 3)
        assert result.deformed_normals.shape == (F, V, 3)
        assert result.blended_dqs.shape == (F, V, 8)

    def test_blended_dqs_are_unit(self):
        """After normalization, blended DQs have ||real|| = 1."""
        verts, normals, tris, weights, bone_dqs = self._make_two_bone_cylinder()
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        real_norms = np.linalg.norm(result.blended_dqs[..., :4], axis=-1)
        np.testing.assert_allclose(real_norms, 1.0, atol=1e-10)

    def test_normals_unit_length(self):
        """Deformed normals remain unit length."""
        verts, normals, tris, weights, bone_dqs = self._make_two_bone_cylinder()
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        n_norms = np.linalg.norm(result.deformed_normals, axis=-1)
        np.testing.assert_allclose(n_norms, 1.0, atol=1e-10)

    def test_volume_preservation_90deg(self):
        """DQS preserves cross-section area at joint midpoint (90° bend).

        This is the canonical DQS advantage over LBS: the cross-section
        at the joint midpoint should remain close to the rest-pose area,
        NOT collapse like LBS candy-wrapper artifact.

        Kavan et al. (2007) Section 5: "the area of the cross-section
        at the joint midpoint is preserved within 1%".
        """
        verts, normals, tris, weights, bone_dqs = self._make_two_bone_cylinder(
            n_frames=10, max_angle=np.pi / 2,
        )
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)

        rest_area = compute_cross_section_area(verts, axis="y", slice_pos=0.0)
        deformed_area = compute_cross_section_area(
            result.deformed_vertices, axis="y", slice_pos=0.0,
        )

        # Volume preservation: deformed area should be within 15% of rest area
        # (relaxed from 1% because our convex-hull approximation is coarser
        # than the paper's analytic measurement)
        ratio = deformed_area / max(rest_area, 1e-12)
        assert ratio > 0.85, (
            f"Volume preservation failed: rest_area={rest_area:.4f}, "
            f"deformed_area={deformed_area:.4f}, ratio={ratio:.4f}"
        )

    def test_pure_translation_skinning(self):
        """Single bone with pure translation moves all vertices."""
        verts = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64)
        normals = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
        weights = np.ones((2, 1), dtype=np.float64)
        t = np.array([5.0, 3.0, 1.0])
        bone_dq = dq_from_rotation_translation(
            np.array([1.0, 0.0, 0.0, 0.0]), t,
        )
        bone_dqs = bone_dq[np.newaxis, np.newaxis, :]  # (1, 1, 8)
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        expected = verts + t[np.newaxis, :]
        np.testing.assert_allclose(result.deformed_vertices[0], expected, atol=1e-10)
        # Normals should be unchanged (no rotation)
        np.testing.assert_allclose(result.deformed_normals[0], normals, atol=1e-10)

    def test_pure_rotation_skinning(self):
        """Single bone with 90° Z rotation rotates all vertices."""
        verts = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
        normals = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
        weights = np.ones((1, 1), dtype=np.float64)
        q = quat_from_axis_angle_batch(
            np.array([[0.0, 0.0, 1.0]]),
            np.array([np.pi / 2]),
        )[0]
        bone_dq = dq_from_rotation_translation(q, np.array([0.0, 0.0, 0.0]))
        bone_dqs = bone_dq[np.newaxis, np.newaxis, :]  # (1, 1, 8)
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        np.testing.assert_allclose(
            result.deformed_vertices[0], [[0.0, 1.0, 0.0]], atol=1e-10,
        )
        np.testing.assert_allclose(
            result.deformed_normals[0], [[0.0, 1.0, 0.0]], atol=1e-10,
        )

    def test_antipodal_correction(self):
        """Antipodal correction prevents tearing when quaternions differ by sign.

        Create two bones where one has q and the other has -q (same rotation).
        Without antipodal correction, the blend would interpolate the "long way".
        With correction, the result should be identical to using the same sign.
        """
        q = quat_from_axis_angle_batch(
            np.array([[0.0, 0.0, 1.0]]),
            np.array([np.pi / 4]),
        )[0]
        t = np.array([0.0, 0.0, 0.0])
        dq_pos = dq_from_rotation_translation(q, t)
        dq_neg = dq_from_rotation_translation(-q, t)  # same rotation, opposite sign

        verts = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
        normals = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
        weights = np.array([[0.5, 0.5]], dtype=np.float64)

        bone_dqs = np.stack([dq_pos, dq_neg], axis=0)[np.newaxis]  # (1, 2, 8)
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)

        # With antipodal correction, both bones should blend to the same rotation
        # The result should be the same as applying q to the vertex
        expected = quat_rotate_points_batch(q[np.newaxis], verts)
        np.testing.assert_allclose(
            result.deformed_vertices[0], expected, atol=1e-10,
        )

    def test_multi_frame_continuity(self):
        """Deformed vertices change smoothly across frames."""
        verts, normals, tris, weights, bone_dqs = self._make_two_bone_cylinder(
            n_frames=20, max_angle=np.pi / 2,
        )
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)
        # Check that consecutive frames don't have huge jumps
        for f in range(1, 20):
            diff = np.max(np.abs(
                result.deformed_vertices[f] - result.deformed_vertices[f - 1]
            ))
            assert diff < 0.5, (
                f"Frame {f}: max vertex displacement = {diff:.4f} (too large)"
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Section 4: Cylinder Mesh & Skin Weights
# ═══════════════════════════════════════════════════════════════════════════

class TestCylinderMesh:
    """Tests for the test cylinder mesh generator."""

    def test_cylinder_vertex_count(self):
        """Cylinder has (radial+0) * (height+1) vertices."""
        verts, normals, tris = create_cylinder_mesh(
            radial_segments=16, height_segments=8,
        )
        expected_v = 16 * (8 + 1)
        assert verts.shape == (expected_v, 3)
        assert normals.shape == (expected_v, 3)

    def test_cylinder_triangle_count(self):
        """Cylinder has radial * height * 2 triangles."""
        verts, normals, tris = create_cylinder_mesh(
            radial_segments=16, height_segments=8,
        )
        expected_t = 16 * 8 * 2
        assert tris.shape == (expected_t, 3)

    def test_cylinder_normals_unit(self):
        """Cylinder normals are unit length."""
        verts, normals, tris = create_cylinder_mesh()
        norms = np.linalg.norm(normals, axis=-1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-10)

    def test_skin_weights_sum_to_one(self):
        """Skinning weights sum to 1.0 for each vertex."""
        verts, _, _ = create_cylinder_mesh()
        weights = compute_cylinder_skin_weights(verts)
        sums = weights.sum(axis=-1)
        np.testing.assert_allclose(sums, 1.0, atol=1e-12)

    def test_skin_weights_non_negative(self):
        """Skinning weights are non-negative."""
        verts, _, _ = create_cylinder_mesh()
        weights = compute_cylinder_skin_weights(verts)
        assert np.all(weights >= 0.0)

    def test_skin_weights_boundary(self):
        """Bottom vertices: w0≈1, w1≈0. Top vertices: w0≈0, w1≈1."""
        verts, _, _ = create_cylinder_mesh(
            height=2.0, height_segments=10,
        )
        weights = compute_cylinder_skin_weights(verts, height=2.0)
        # Bottom vertices (y ≈ -1.0)
        bottom_mask = verts[:, 1] < -0.8
        assert np.all(weights[bottom_mask, 0] > 0.9)
        # Top vertices (y ≈ 1.0)
        top_mask = verts[:, 1] > 0.8
        assert np.all(weights[top_mask, 1] > 0.9)


# ═══════════════════════════════════════════════════════════════════════════
#  Section 5: Backend Registry & Manifest Contract
# ═══════════════════════════════════════════════════════════════════════════

class TestPseudo3DShellBackendRegistry:
    """Tests for backend registration and discovery."""

    def test_backend_registered(self):
        """pseudo_3d_shell is discoverable in the registry."""
        reg = get_registry()
        entry = reg.get("pseudo_3d_shell")
        assert entry is not None, "pseudo_3d_shell not found in registry"

    def test_backend_meta_fields(self):
        """Backend meta has correct name, version, families."""
        reg = get_registry()
        meta, cls = reg.get_or_raise("pseudo_3d_shell")
        assert meta.name == "pseudo_3d_shell"
        assert meta.version == "1.1.0"
        assert ArtifactFamily.MESH_OBJ.value in meta.artifact_families

    def test_backend_type_enum(self):
        """PSEUDO_3D_SHELL is in BackendType enum."""
        assert hasattr(BackendType, "PSEUDO_3D_SHELL")
        assert BackendType.PSEUDO_3D_SHELL.value == "pseudo_3d_shell"

    def test_backend_aliases(self):
        """Backend type aliases resolve correctly."""
        from mathart.core.backend_types import backend_type_value
        assert backend_type_value("pseudo3d_shell") == "pseudo_3d_shell"
        assert backend_type_value("paper_doll_shell") == "pseudo_3d_shell"
        assert backend_type_value("dqs_mesh_shell") == "pseudo_3d_shell"
        assert backend_type_value("mesh_shell_dqs") == "pseudo_3d_shell"


class TestPseudo3DShellBackendExecution:
    """Tests for backend validate_config and execute."""

    def test_validate_config_defaults(self):
        """validate_config fills defaults for missing fields."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        validated, warnings = backend.validate_config({})
        assert validated["_use_demo_mesh"] is True
        assert validated["_use_demo_animation"] is True
        assert len(warnings) == 2

    def test_validate_config_with_data(self):
        """validate_config accepts provided data without warnings."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        verts, normals, tris = create_cylinder_mesh()
        weights = compute_cylinder_skin_weights(verts)
        bone_dqs = dq_identity((1, 2))
        config = {
            "base_vertices": verts,
            "base_normals": normals,
            "triangles": tris,
            "skin_weights": weights,
            "bone_dqs": bone_dqs,
        }
        validated, warnings = backend.validate_config(config)
        assert not validated.get("_use_demo_mesh", False)
        assert not validated.get("_use_demo_animation", False)

    def test_execute_demo_returns_manifest(self):
        """Execute with demo data returns a valid ArtifactManifest."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = {"output_dir": tmpdir}
            manifest = backend.execute(context)
            assert isinstance(manifest, ArtifactManifest)
            assert manifest.artifact_family == ArtifactFamily.MESH_OBJ.value
            assert manifest.backend_type == "pseudo_3d_shell"
            assert "mesh" in manifest.outputs
            assert manifest.metadata["vertex_count"] > 0
            assert manifest.metadata["face_count"] > 0
            assert manifest.metadata["skinning_method"] == "DQS"
            assert manifest.metadata["volume_preservation"] is True

    def test_execute_output_files_exist(self):
        """Execute creates mesh NPZ and metadata JSON files."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = {"output_dir": tmpdir}
            manifest = backend.execute(context)
            mesh_path = Path(manifest.outputs["mesh"])
            meta_path = Path(manifest.outputs["metadata"])
            assert mesh_path.exists()
            assert meta_path.exists()
            # Verify NPZ contents
            with np.load(str(mesh_path)) as data:
                assert "vertices" in data
                assert "normals" in data
                assert "triangles" in data
            # Verify JSON contents
            meta = json.loads(meta_path.read_text())
            assert meta["backend"] == "pseudo_3d_shell"
            assert meta["skinning_method"] == "dual_quaternion_linear_blending"
            assert meta["volume_preservation"] is True
            assert meta["antipodal_correction"] is True

    def test_execute_with_custom_mesh(self):
        """Execute with custom mesh data produces correct vertex count."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        verts, normals, tris = create_cylinder_mesh(
            radial_segments=8, height_segments=4,
        )
        weights = compute_cylinder_skin_weights(verts)
        bone_dqs = dq_identity((3, 2))  # 3 frames, 2 bones
        with tempfile.TemporaryDirectory() as tmpdir:
            context = {
                "output_dir": tmpdir,
                "base_vertices": verts,
                "base_normals": normals,
                "triangles": tris,
                "skin_weights": weights,
                "bone_dqs": bone_dqs,
            }
            manifest = backend.execute(context)
            assert manifest.metadata["vertex_count"] == verts.shape[0]
            assert manifest.metadata["frame_count"] == 3

    def test_manifest_quality_metrics(self):
        """Manifest quality_metrics include DQS guard flags."""
        reg = get_registry()
        _, cls = reg.get_or_raise("pseudo_3d_shell")
        backend = cls()
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = backend.execute({"output_dir": tmpdir})
            qm = manifest.quality_metrics
            assert qm["dqs_tensorized"] is True
            assert qm["zero_scalar_loops"] is True
            assert qm["antipodal_corrected"] is True
            assert qm["normalized"] is True


# ═══════════════════════════════════════════════════════════════════════════
#  Section 6: Cross-Section Area Utility
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossSectionArea:
    """Tests for the cross-section area measurement utility."""

    def test_undeformed_cylinder_area(self):
        """Undeformed cylinder cross-section ≈ π * r²."""
        verts, _, _ = create_cylinder_mesh(radius=0.5, height_segments=20)
        area = compute_cross_section_area(verts, axis="y", slice_pos=0.0)
        expected = math.pi * 0.5 ** 2
        # Convex hull of discrete points approximates circle area
        assert abs(area - expected) / expected < 0.05, (
            f"area={area:.4f}, expected≈{expected:.4f}"
        )

    def test_empty_slice_returns_zero(self):
        """Slice with no vertices returns 0."""
        verts = np.array([[0.0, 10.0, 0.0]])
        area = compute_cross_section_area(verts, axis="y", slice_pos=0.0)
        assert area == 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  Section 7: Integration — DQS Engine + Mesh3D
# ═══════════════════════════════════════════════════════════════════════════

class TestDQSMesh3DIntegration:
    """Integration tests: DQS output feeds into Mesh3D (SESSION-106)."""

    def test_deformed_mesh_to_mesh3d(self):
        """Deformed vertices/normals can construct a valid Mesh3D."""
        from mathart.animation.orthographic_pixel_render import Mesh3D

        verts, normals, tris = create_cylinder_mesh()
        weights = compute_cylinder_skin_weights(verts)
        bone_dqs = dq_identity((1, 2))
        result = tensorized_dqs_skin(verts, normals, weights, bone_dqs)

        mesh = Mesh3D(
            vertices=result.deformed_vertices[0],
            normals=result.deformed_normals[0],
            triangles=tris,
            colors=np.full((verts.shape[0], 3), 180, dtype=np.uint8),
        )
        assert mesh.vertices.shape[0] == verts.shape[0]
        assert mesh.normals.shape[0] == verts.shape[0]
        assert mesh.triangles.shape[0] == tris.shape[0]
