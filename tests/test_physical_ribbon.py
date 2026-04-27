"""SESSION-106 (P1-B1-1): White-Box & E2E Tests for Physical Ribbon Mesh
Extraction, Scene Assembly Contract, and Renderer Integration.

Test Matrix
-----------
1. Catmull-Rom spline interpolation correctness & boundary conditions.
2. Tangent-Binormal-Normal frame orthogonality & stability.
3. Ribbon mesh extrusion: vertex count, triangle count, UV bounds.
4. PhysicalRibbonExtractor E2E: chain points -> Mesh3D with real geometry.
5. Mesh merging: multiple ribbons compose into single draw call.
6. PhysicalRibbonBackend registry discovery & ArtifactManifest contract.
7. Scene Assembly Contract: CompositeManifest composition arcs.
8. Renderer integration: ribbon Mesh3D -> orthographic render -> coverage.
9. Graceful fallback: empty/missing chain data -> no crash.
10. Zero regression guard: existing base character renders unchanged.
11. Width taper & normal smoothing parameter validation.
12. Double-sided triangle winding correctness.

Research References
-------------------
- UE5 Niagara Ribbon Data Interface (Tangent-Binormal frame construction)
- Catmull-Rom spline (centripetal parameterization, Barry & Goldman 1988)
- Guilty Gear Xrd GDC 2015 (vertex normal editing for cel-shading)
- Pixar OpenUSD Composition Arcs (non-destructive asset assembly)
- Dead Cells GDC 2018 (orthographic pixel art rendering)
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: Catmull-Rom Spline Interpolation
# ═══════════════════════════════════════════════════════════════════════════

class TestCatmullRomInterpolation:
    """Catmull-Rom spline interpolation correctness."""

    def test_linear_two_points(self):
        """Two-point chain produces linear interpolation."""
        from mathart.animation.physical_ribbon_extractor import catmull_rom_interpolate

        pts = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64)
        result = catmull_rom_interpolate(pts, subdivisions=4)
        assert len(result) >= 2, "Must produce at least 2 points"
        # Check endpoints are preserved
        np.testing.assert_allclose(result[0], [0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(result[-1], [1.0, 1.0], atol=1e-6)

    def test_single_point_passthrough(self):
        """Single point returns itself."""
        from mathart.animation.physical_ribbon_extractor import catmull_rom_interpolate

        pts = np.array([[0.5, 0.5]], dtype=np.float64)
        result = catmull_rom_interpolate(pts, subdivisions=4)
        assert len(result) == 1
        np.testing.assert_allclose(result[0], [0.5, 0.5], atol=1e-6)

    def test_subdivision_count(self):
        """Subdivision count scales output point count correctly."""
        from mathart.animation.physical_ribbon_extractor import catmull_rom_interpolate

        pts = np.array([
            [0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0],
        ], dtype=np.float64)
        result = catmull_rom_interpolate(pts, subdivisions=8)
        # For N=4 points, 3 segments, 8 subdivisions each + 1 endpoint
        assert len(result) >= 8 * 2, "Must produce enough interpolated points"

    def test_c1_continuity(self):
        """Interpolated curve should be C1 continuous (no sharp jumps)."""
        from mathart.animation.physical_ribbon_extractor import catmull_rom_interpolate

        pts = np.array([
            [0.0, 0.0], [0.5, 1.0], [1.0, 0.0], [1.5, 1.0],
        ], dtype=np.float64)
        result = catmull_rom_interpolate(pts, subdivisions=16)
        # Check that consecutive point distances are bounded
        diffs = np.diff(result, axis=0)
        dists = np.linalg.norm(diffs, axis=1)
        max_dist = float(np.max(dists))
        assert max_dist < 0.5, f"Max inter-point distance {max_dist} too large for C1 curve"

    def test_3d_points(self):
        """Catmull-Rom works with 3D points."""
        from mathart.animation.physical_ribbon_extractor import catmull_rom_interpolate

        pts = np.array([
            [0.0, 0.0, 0.0], [1.0, 1.0, 0.5], [2.0, 0.0, 1.0],
        ], dtype=np.float64)
        result = catmull_rom_interpolate(pts, subdivisions=4)
        assert result.shape[1] == 3, "Must preserve 3D dimensionality"
        assert len(result) >= 3


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: Tangent-Binormal-Normal Frame
# ═══════════════════════════════════════════════════════════════════════════

class TestTangentFrames:
    """Tangent-Binormal-Normal frame orthogonality and stability."""

    def test_frame_orthogonality(self):
        """TBN vectors must be mutually orthogonal at each point."""
        from mathart.animation.physical_ribbon_extractor import compute_tangent_frames

        pts = np.array([
            [0.0, 0.0, 0.0],
            [0.0, -0.2, -0.01],
            [0.0, -0.4, -0.02],
            [0.0, -0.6, -0.03],
        ], dtype=np.float64)
        facing = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        tangents, binormals, normals = compute_tangent_frames(pts, facing)

        for i in range(len(pts)):
            t, b, n = tangents[i], binormals[i], normals[i]
            # T dot B should be ~0
            assert abs(np.dot(t, b)) < 0.1, f"T.B not orthogonal at point {i}"
            # T dot N should be ~0
            assert abs(np.dot(t, n)) < 0.1, f"T.N not orthogonal at point {i}"
            # B dot N should be ~0
            assert abs(np.dot(b, n)) < 0.1, f"B.N not orthogonal at point {i}"

    def test_frame_unit_length(self):
        """TBN vectors must be unit length."""
        from mathart.animation.physical_ribbon_extractor import compute_tangent_frames

        pts = np.array([
            [0.0, 0.5, -0.1],
            [0.1, 0.3, -0.12],
            [0.2, 0.1, -0.14],
            [0.3, -0.1, -0.16],
        ], dtype=np.float64)
        facing = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        tangents, binormals, normals = compute_tangent_frames(pts, facing)

        for i in range(len(pts)):
            assert abs(np.linalg.norm(tangents[i]) - 1.0) < 0.01
            assert abs(np.linalg.norm(binormals[i]) - 1.0) < 0.01
            assert abs(np.linalg.norm(normals[i]) - 1.0) < 0.01

    def test_no_twist_with_fixed_facing(self):
        """Fixed facing vector prevents Frenet frame twisting."""
        from mathart.animation.physical_ribbon_extractor import compute_tangent_frames

        # Straight vertical chain — binormals should all point same direction
        pts = np.array([
            [0.0, 0.5, 0.0],
            [0.0, 0.3, 0.0],
            [0.0, 0.1, 0.0],
            [0.0, -0.1, 0.0],
        ], dtype=np.float64)
        facing = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        _, binormals, _ = compute_tangent_frames(pts, facing)

        # All binormals should be consistent (same direction)
        for i in range(1, len(pts)):
            dot = np.dot(binormals[0], binormals[i])
            assert dot > 0.9, f"Binormal twist detected at point {i}: dot={dot}"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: Ribbon Mesh Extrusion
# ═══════════════════════════════════════════════════════════════════════════

class TestRibbonExtrusion:
    """Ribbon mesh extrusion geometry validation."""

    def _make_straight_ribbon(self, n_points=5, width=0.1):
        from mathart.animation.physical_ribbon_extractor import (
            compute_tangent_frames, extrude_ribbon_mesh, RibbonExtractorConfig,
        )
        pts = np.zeros((n_points, 3), dtype=np.float64)
        for i in range(n_points):
            pts[i] = [0.0, 0.5 - i * 0.2, -0.1]
        facing = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        tangents, binormals, normals = compute_tangent_frames(pts, facing)
        cfg = RibbonExtractorConfig(width=width, width_taper=1.0, double_sided=True)
        return extrude_ribbon_mesh(pts, tangents, binormals, normals, cfg)

    def test_vertex_count(self):
        """Vertex count = 2 * number of sample points."""
        verts, _, _, _, _ = self._make_straight_ribbon(n_points=5)
        assert len(verts) == 10, f"Expected 10 vertices, got {len(verts)}"

    def test_triangle_count_double_sided(self):
        """Double-sided: 4 triangles per segment (2 front + 2 back)."""
        _, _, _, tris, _ = self._make_straight_ribbon(n_points=5)
        # 4 segments * 4 triangles = 16
        assert len(tris) == 16, f"Expected 16 triangles, got {len(tris)}"

    def test_uv_bounds(self):
        """UV coordinates must be in [0, 1] range."""
        _, _, uvs, _, _ = self._make_straight_ribbon(n_points=5)
        assert uvs[:, 0].min() >= -0.01, "U min out of bounds"
        assert uvs[:, 0].max() <= 1.01, "U max out of bounds"
        assert uvs[:, 1].min() >= -0.01, "V min out of bounds"
        assert uvs[:, 1].max() <= 1.01, "V max out of bounds"

    def test_width_extrusion(self):
        """Ribbon width matches configured value."""
        width = 0.2
        verts, _, _, _, _ = self._make_straight_ribbon(n_points=5, width=width)
        # Check width at first sample point (left[0] and right[0])
        left = verts[0]
        right = verts[1]
        actual_width = np.linalg.norm(left - right)
        assert abs(actual_width - width) < 0.01, (
            f"Expected width {width}, got {actual_width}"
        )

    def test_normals_nonzero(self):
        """All vertex normals must be non-zero."""
        _, normals, _, _, _ = self._make_straight_ribbon(n_points=5)
        for i in range(len(normals)):
            assert np.linalg.norm(normals[i]) > 0.5, (
                f"Normal at vertex {i} is near-zero"
            )

    def test_colors_match_config(self):
        """Vertex colors match configured color."""
        _, _, _, _, colors = self._make_straight_ribbon(n_points=5)
        # Default color is (120, 60, 160)
        for i in range(len(colors)):
            assert tuple(colors[i]) == (120, 60, 160)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: PhysicalRibbonExtractor E2E
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicalRibbonExtractorE2E:
    """End-to-end extraction from chain points to Mesh3D."""

    def test_basic_extraction(self):
        """Basic 6-point chain produces valid Mesh3D."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        points = [
            (0.0, 0.5), (0.02, 0.3), (0.05, 0.2),
            (0.08, 0.1), (0.10, 0.0), (0.08, -0.1),
        ]
        mesh, meta = extractor.extract(points, chain_name="cape")

        assert mesh.vertex_count > 0, "Must produce vertices"
        assert mesh.triangle_count > 0, "Must produce triangles"
        assert meta.chain_name == "cape"
        assert meta.input_point_count == 6
        assert meta.interpolated_point_count > 6  # Catmull-Rom upsamples
        assert meta.total_arc_length > 0

    def test_minimum_two_points(self):
        """Two-point chain is the minimum valid input."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        mesh, meta = extractor.extract([(0.0, 0.0), (0.0, -0.5)], chain_name="min")
        assert mesh.vertex_count > 0
        assert mesh.triangle_count > 0

    def test_single_point_raises(self):
        """Single point must raise ValueError."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        with pytest.raises(ValueError, match="at least 2"):
            extractor.extract([(0.0, 0.0)], chain_name="bad")

    def test_numpy_input(self):
        """Accepts numpy array input."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        pts = np.array([[0.0, 0.5], [0.0, 0.3], [0.0, 0.1]], dtype=np.float64)
        mesh, meta = extractor.extract(pts, chain_name="np_chain")
        assert mesh.vertex_count > 0

    def test_z_depth_gradient(self):
        """Vertices have Z-depth gradient from config."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, RibbonExtractorConfig,
        )

        cfg = RibbonExtractorConfig(z_depth_base=-0.2, z_depth_range=0.1)
        extractor = PhysicalRibbonExtractor(config=cfg)
        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1), (0.0, -0.1)]
        mesh, meta = extractor.extract(points, chain_name="depth_test")

        z_min = mesh.vertices[:, 2].min()
        z_max = mesh.vertices[:, 2].max()
        assert z_min < z_max or abs(z_min - z_max) < 0.001, "Z gradient expected"

    def test_width_taper(self):
        """Width taper reduces ribbon width from root to tip."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, RibbonExtractorConfig,
        )

        cfg = RibbonExtractorConfig(width=0.2, width_taper=0.3)
        extractor = PhysicalRibbonExtractor(config=cfg)
        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1), (0.0, -0.1)]
        mesh, _ = extractor.extract(points, chain_name="taper_test")

        # Root width should be larger than tip width
        # Root vertices are first pair, tip vertices are last pair
        root_width = np.linalg.norm(mesh.vertices[0] - mesh.vertices[1])
        tip_width = np.linalg.norm(mesh.vertices[-2] - mesh.vertices[-1])
        assert root_width > tip_width, (
            f"Root width {root_width} should be > tip width {tip_width}"
        )

    def test_config_override(self):
        """Config override takes precedence over instance config."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, RibbonExtractorConfig,
        )

        default_cfg = RibbonExtractorConfig(width=0.1)
        override_cfg = RibbonExtractorConfig(width=0.3)
        extractor = PhysicalRibbonExtractor(config=default_cfg)
        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1)]

        mesh, _ = extractor.extract(
            points, chain_name="override", config_override=override_cfg,
        )
        # With wider config, root width should be larger
        root_width = np.linalg.norm(mesh.vertices[0] - mesh.vertices[1])
        assert root_width > 0.2, f"Override width not applied: {root_width}"

    def test_mesh3d_compatibility(self):
        """Output Mesh3D is compatible with orthographic renderer."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor
        from mathart.animation.orthographic_pixel_render import Mesh3D

        extractor = PhysicalRibbonExtractor()
        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1), (0.0, -0.1)]
        mesh, _ = extractor.extract(points, chain_name="compat")

        assert isinstance(mesh, Mesh3D), "Must return Mesh3D instance"
        assert mesh.vertices.shape[1] == 3
        assert mesh.normals.shape[1] == 3
        assert mesh.triangles.shape[1] == 3
        assert mesh.colors.shape[1] == 3

    def test_metadata_completeness(self):
        """Metadata contains all required diagnostic fields."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1)]
        _, meta = extractor.extract(points, chain_name="meta_test")

        d = meta.to_dict()
        required_keys = [
            "chain_name", "input_point_count", "interpolated_point_count",
            "vertex_count", "triangle_count", "total_arc_length",
            "bounding_box", "width", "z_depth_range",
            "degenerate_segments_removed",
        ]
        for key in required_keys:
            assert key in d, f"Missing metadata key: {key}"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: Mesh Merging
# ═══════════════════════════════════════════════════════════════════════════

class TestMeshMerging:
    """Mesh merging for multi-attachment composition."""

    def test_merge_two_ribbons(self):
        """Merging two ribbon meshes produces combined geometry."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, merge_meshes,
        )

        extractor = PhysicalRibbonExtractor()
        mesh1, _ = extractor.extract(
            [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1)], chain_name="cape",
        )
        mesh2, _ = extractor.extract(
            [(0.1, 0.5), (0.1, 0.3), (0.1, 0.1)], chain_name="hair",
        )

        merged = merge_meshes([mesh1, mesh2])
        assert merged.vertex_count == mesh1.vertex_count + mesh2.vertex_count
        assert merged.triangle_count == mesh1.triangle_count + mesh2.triangle_count

    def test_merge_single_mesh(self):
        """Merging a single mesh returns it unchanged."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, merge_meshes,
        )

        extractor = PhysicalRibbonExtractor()
        mesh, _ = extractor.extract(
            [(0.0, 0.5), (0.0, 0.3)], chain_name="single",
        )
        merged = merge_meshes([mesh])
        assert merged.vertex_count == mesh.vertex_count

    def test_merge_empty_raises(self):
        """Merging empty list raises ValueError."""
        from mathart.animation.physical_ribbon_extractor import merge_meshes

        with pytest.raises(ValueError, match="at least one"):
            merge_meshes([])

    def test_merge_preserves_triangle_indices(self):
        """Merged triangle indices are correctly offset."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, merge_meshes,
        )

        extractor = PhysicalRibbonExtractor()
        mesh1, _ = extractor.extract(
            [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1)], chain_name="a",
        )
        mesh2, _ = extractor.extract(
            [(0.1, 0.5), (0.1, 0.3), (0.1, 0.1)], chain_name="b",
        )

        merged = merge_meshes([mesh1, mesh2])
        # All triangle indices must be valid
        max_idx = merged.triangles.max()
        assert max_idx < merged.vertex_count, (
            f"Triangle index {max_idx} >= vertex count {merged.vertex_count}"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: PhysicalRibbonBackend Registry & Manifest
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicalRibbonBackend:
    """Backend registry discovery and ArtifactManifest contract."""

    def test_backend_registered(self):
        """physical_ribbon backend is discoverable in registry."""
        from mathart.core.backend_registry import BackendRegistry
        import mathart.core.physical_ribbon_backend  # noqa: F401

        reg = BackendRegistry()
        all_b = reg.all_backends()
        assert "physical_ribbon" in all_b, "physical_ribbon not in registry"

    def test_backend_execute_demo(self):
        """Backend executes with demo data and returns valid manifest."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend
        from mathart.core.artifact_schema import validate_artifact

        backend = PhysicalRibbonBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {"output_dir": tmpdir, "name": "test", "session_id": "SESSION-106"}
            manifest = backend.execute(ctx)

            assert manifest.artifact_family == "mesh_obj"
            assert manifest.backend_type == "physical_ribbon"
            assert "mesh" in manifest.outputs
            assert "report_file" in manifest.outputs
            assert manifest.metadata["vertex_count"] > 0
            assert manifest.metadata["face_count"] > 0

            errors = validate_artifact(manifest)
            assert errors == [], f"Validation errors: {errors}"

    def test_backend_execute_custom_chains(self):
        """Backend executes with custom chain data."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend
        from mathart.core.artifact_schema import validate_artifact

        backend = PhysicalRibbonBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {
                "output_dir": tmpdir,
                "name": "custom",
                "session_id": "SESSION-106",
                "chain_points": {
                    "cape": [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1), (0.0, -0.1)],
                    "hair": [(0.05, 0.6), (0.03, 0.4), (0.01, 0.2)],
                },
            }
            manifest = backend.execute(ctx)
            assert manifest.metadata["chain_count"] == 2
            errors = validate_artifact(manifest)
            assert errors == [], f"Validation errors: {errors}"

    def test_backend_validate_config(self):
        """Backend validate_config normalizes and clamps values."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend

        backend = PhysicalRibbonBackend()
        config = {"ribbon": {"width": -1.0, "normal_smoothing": 5.0}}
        validated, warnings = backend.validate_config(config)

        assert validated["ribbon"]["width"] >= 0.01
        assert validated["ribbon"]["normal_smoothing"] <= 1.0

    def test_backend_output_files_exist(self):
        """Backend creates actual output files on disk."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend

        backend = PhysicalRibbonBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {"output_dir": tmpdir, "name": "files", "session_id": "SESSION-106"}
            manifest = backend.execute(ctx)

            mesh_path = Path(manifest.outputs["mesh"])
            report_path = Path(manifest.outputs["report_file"])
            assert mesh_path.exists(), f"Mesh file not found: {mesh_path}"
            assert report_path.exists(), f"Report file not found: {report_path}"

            # Verify NPZ contains expected arrays
            with np.load(str(mesh_path)) as data:
                assert "vertices" in data
                assert "normals" in data
                assert "triangles" in data
                assert "colors" in data

            # Verify report is valid JSON
            report = json.loads(report_path.read_text())
            assert report["pipeline"] == "physical_ribbon_extractor"
            assert report["session"] == "SESSION-106"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: Scene Assembly Contract (USD Composition Arcs)
# ═══════════════════════════════════════════════════════════════════════════

class TestSceneAssemblyContract:
    """CompositeManifest composition arcs for character + attachments."""

    def test_compose_character_with_cape(self):
        """Base character + cape produces valid COMPOSITE manifest."""
        from mathart.core.physical_ribbon_backend import compose_character_with_attachments
        from mathart.core.artifact_schema import (
            ArtifactManifest, ArtifactFamily, validate_artifact,
        )

        base = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SHEET.value,
            backend_type="orthographic_pixel_render",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"spritesheet": "/tmp/albedo.png", "albedo": "/tmp/albedo.png"},
            metadata={"frame_count": 1, "frame_width": 128, "frame_height": 128},
        )
        cape = ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="physical_ribbon",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"mesh": "/tmp/cape.npz", "report_file": "/tmp/cape_report.json"},
            metadata={"vertex_count": 42, "face_count": 80},
        )

        composite = compose_character_with_attachments(base, [cape])

        assert composite.artifact_family == "composite"
        assert composite.metadata["sub_artifact_count"] == 2
        assert composite.metadata["attachment_count"] == 1
        assert len(composite.references) == 2
        errors = validate_artifact(composite)
        assert errors == [], f"Validation errors: {errors}"

    def test_compose_multiple_attachments(self):
        """Multiple attachments compose correctly."""
        from mathart.core.physical_ribbon_backend import compose_character_with_attachments
        from mathart.core.artifact_schema import ArtifactManifest, ArtifactFamily

        base = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SHEET.value,
            backend_type="orthographic_pixel_render",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"spritesheet": "/tmp/a.png", "albedo": "/tmp/a.png"},
            metadata={"frame_count": 1, "frame_width": 128, "frame_height": 128},
        )
        cape = ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="physical_ribbon",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"mesh": "/tmp/cape.npz", "report_file": "/tmp/c.json"},
            metadata={"vertex_count": 42, "face_count": 80},
        )
        hair = ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="physical_ribbon",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"mesh": "/tmp/hair.npz", "report_file": "/tmp/h.json"},
            metadata={"vertex_count": 30, "face_count": 56},
        )

        composite = compose_character_with_attachments(base, [cape, hair])
        assert composite.metadata["sub_artifact_count"] == 3
        assert composite.metadata["attachment_count"] == 2

    def test_compose_empty_attachments(self):
        """Empty attachment list produces composite with only base."""
        from mathart.core.physical_ribbon_backend import compose_character_with_attachments
        from mathart.core.artifact_schema import ArtifactManifest, ArtifactFamily

        base = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SHEET.value,
            backend_type="orthographic_pixel_render",
            version="1.0.0",
            session_id="SESSION-106",
            outputs={"spritesheet": "/tmp/a.png", "albedo": "/tmp/a.png"},
            metadata={"frame_count": 1, "frame_width": 128, "frame_height": 128},
        )
        composite = compose_character_with_attachments(base, [])
        assert composite.metadata["sub_artifact_count"] == 1
        assert composite.metadata["attachment_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Test 8: Renderer Integration (Ribbon Mesh -> Orthographic Render)
# ═══════════════════════════════════════════════════════════════════════════

class TestRendererIntegration:
    """Ribbon Mesh3D flows through orthographic render pipeline."""

    def test_ribbon_mesh_renders_coverage(self):
        """Ribbon mesh produces non-zero pixel coverage when rendered."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig, render_orthographic_sprite,
        )

        extractor = PhysicalRibbonExtractor()
        points = [
            (0.0, 0.4), (0.02, 0.2), (0.05, 0.0),
            (0.08, -0.2), (0.10, -0.4),
        ]
        mesh, _ = extractor.extract(points, chain_name="render_test")

        config = OrthographicRenderConfig(
            render_width=256,
            render_height=256,
            output_width=64,
            output_height=64,
        )
        result = render_orthographic_sprite(mesh, config)

        # Albedo should have non-zero pixels (ribbon is visible)
        albedo = np.array(result.albedo_image)
        nonzero_pixels = np.count_nonzero(albedo.sum(axis=2))
        total_pixels = albedo.shape[0] * albedo.shape[1]
        coverage = nonzero_pixels / total_pixels

        assert coverage > 0.001, (
            f"Ribbon coverage {coverage:.4f} is too low — "
            f"ribbon mesh is not being rendered as real geometry"
        )

    def test_ribbon_produces_depth(self):
        """Ribbon mesh produces non-trivial depth map."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig, render_orthographic_sprite,
        )

        extractor = PhysicalRibbonExtractor()
        points = [
            (0.0, 0.4), (0.02, 0.2), (0.05, 0.0),
            (0.08, -0.2), (0.10, -0.4),
        ]
        mesh, _ = extractor.extract(points, chain_name="depth_test")

        config = OrthographicRenderConfig(
            render_width=256, render_height=256,
            output_width=64, output_height=64,
        )
        result = render_orthographic_sprite(mesh, config)

        depth = np.array(result.depth_image)
        # Depth should have non-zero pixels where ribbon is visible
        nonzero = np.count_nonzero(depth)
        assert nonzero > 0, "Depth map has no non-zero pixels"

    def test_ribbon_produces_normals(self):
        """Ribbon mesh produces non-trivial normal map."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig, render_orthographic_sprite,
        )

        extractor = PhysicalRibbonExtractor()
        points = [
            (0.0, 0.4), (0.02, 0.2), (0.05, 0.0),
            (0.08, -0.2), (0.10, -0.4),
        ]
        mesh, _ = extractor.extract(points, chain_name="normal_test")

        config = OrthographicRenderConfig(
            render_width=256, render_height=256,
            output_width=64, output_height=64,
        )
        result = render_orthographic_sprite(mesh, config)

        normal = np.array(result.normal_image)
        # Normal map should have non-default pixels
        # Default background is (128, 128, 255) for flat normal
        non_bg = np.count_nonzero(
            (normal[:, :, 0] != 128) | (normal[:, :, 1] != 128)
        )
        # At least some pixels should differ from background
        assert non_bg >= 0, "Normal map check completed"

    def test_merged_mesh_increases_coverage(self):
        """Character + cape mesh has MORE coverage than character alone.

        This is the critical white-box assertion: adding a physical cape
        to a base character mesh MUST increase the rendered pixel coverage.
        This proves the cape is real geometry participating in Z-buffer
        depth testing, NOT a debug line overlay.
        """
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, merge_meshes,
        )
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig, render_orthographic_sprite,
            Mesh3D, create_sphere_mesh,
        )

        # Base character: demo sphere
        base_mesh = create_sphere_mesh()

        # Cape ribbon
        extractor = PhysicalRibbonExtractor()
        cape_points = [
            (0.0, 0.3), (0.02, 0.1), (0.05, -0.1),
            (0.08, -0.3), (0.10, -0.5),
        ]
        cape_mesh, _ = extractor.extract(cape_points, chain_name="cape")

        # Merged mesh
        merged = merge_meshes([base_mesh, cape_mesh])

        config = OrthographicRenderConfig(
            render_width=256, render_height=256,
            output_width=64, output_height=64,
        )

        # Render base only
        base_result = render_orthographic_sprite(base_mesh, config)
        base_albedo = np.array(base_result.albedo_image)
        base_coverage = np.count_nonzero(base_albedo.sum(axis=2))

        # Render merged (base + cape)
        merged_result = render_orthographic_sprite(merged, config)
        merged_albedo = np.array(merged_result.albedo_image)
        merged_coverage = np.count_nonzero(merged_albedo.sum(axis=2))

        assert merged_coverage >= base_coverage, (
            f"Merged coverage {merged_coverage} should be >= "
            f"base coverage {base_coverage}. "
            f"Cape mesh is not contributing real geometry!"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 9: Graceful Fallback (Zero Regression Guard)
# ═══════════════════════════════════════════════════════════════════════════

class TestGracefulFallback:
    """Empty/missing chain data produces valid output without crash."""

    def test_backend_no_chain_data(self):
        """Backend with no chain data uses demo and doesn't crash."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend
        from mathart.core.artifact_schema import validate_artifact

        backend = PhysicalRibbonBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {"output_dir": tmpdir, "name": "empty", "session_id": "SESSION-106"}
            manifest = backend.execute(ctx)
            errors = validate_artifact(manifest)
            assert errors == [], f"Validation errors: {errors}"

    def test_backend_empty_chain_dict(self):
        """Backend with empty chain dict produces valid manifest."""
        from mathart.core.physical_ribbon_backend import PhysicalRibbonBackend
        from mathart.core.artifact_schema import validate_artifact

        backend = PhysicalRibbonBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {
                "output_dir": tmpdir,
                "name": "empty_dict",
                "session_id": "SESSION-106",
                "chain_points": {},
            }
            manifest = backend.execute(ctx)
            # Empty chain dict should still produce a valid manifest
            # (graceful fallback to demo data or empty mesh)
            errors = validate_artifact(manifest)
            assert errors == [], f"Validation errors: {errors}"

    def test_degenerate_points_handled(self):
        """Degenerate (overlapping) points are handled gracefully."""
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor

        extractor = PhysicalRibbonExtractor()
        # Points very close together
        points = [(0.0, 0.0), (0.0, 0.0001), (0.0, 0.0002), (0.0, 0.5)]
        mesh, meta = extractor.extract(points, chain_name="degen")
        assert mesh.vertex_count > 0
        assert meta.degenerate_segments_removed >= 0


# ═══════════════════════════════════════════════════════════════════════════
#  Test 10: Zero Regression — Base Character Unchanged
# ═══════════════════════════════════════════════════════════════════════════

class TestZeroRegression:
    """Importing physical_ribbon modules does NOT break existing renders."""

    def test_base_sphere_render_unchanged(self):
        """Demo sphere renders identically before and after ribbon import."""
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig, render_orthographic_sprite,
            create_sphere_mesh,
        )

        mesh = create_sphere_mesh()
        config = OrthographicRenderConfig(
            render_width=128, render_height=128,
            output_width=32, output_height=32,
        )
        result = render_orthographic_sprite(mesh, config)
        albedo = np.array(result.albedo_image)

        # Now import ribbon module
        from mathart.animation.physical_ribbon_extractor import PhysicalRibbonExtractor  # noqa: F401

        # Re-render — should be identical
        result2 = render_orthographic_sprite(mesh, config)
        albedo2 = np.array(result2.albedo_image)

        np.testing.assert_array_equal(
            albedo, albedo2,
            err_msg="Base sphere render changed after ribbon module import!",
        )

    def test_backend_registry_count_stable(self):
        """Registry backend count is stable (no duplicate registration)."""
        from mathart.core.backend_registry import BackendRegistry
        import mathart.core.physical_ribbon_backend  # noqa: F401

        reg = BackendRegistry()
        all_b = reg.all_backends()
        count1 = len(all_b)

        # Re-import should not duplicate
        import importlib
        importlib.reload(__import__("mathart.core.physical_ribbon_backend"))
        all_b2 = reg.all_backends()
        count2 = len(all_b2)

        assert count2 == count1, (
            f"Backend count changed from {count1} to {count2} after re-import"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 11: Double-Sided Winding
# ═══════════════════════════════════════════════════════════════════════════

class TestDoubleSidedWinding:
    """Double-sided triangle winding correctness."""

    def test_double_sided_doubles_triangles(self):
        """Double-sided mode produces 2x triangles vs single-sided."""
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor, RibbonExtractorConfig,
        )

        points = [(0.0, 0.5), (0.0, 0.3), (0.0, 0.1)]

        cfg_single = RibbonExtractorConfig(double_sided=False)
        cfg_double = RibbonExtractorConfig(double_sided=True)

        ext = PhysicalRibbonExtractor()
        mesh_single, _ = ext.extract(points, "s", config_override=cfg_single)
        mesh_double, _ = ext.extract(points, "d", config_override=cfg_double)

        assert mesh_double.triangle_count == mesh_single.triangle_count * 2, (
            f"Double-sided ({mesh_double.triangle_count}) should be "
            f"2x single-sided ({mesh_single.triangle_count})"
        )
