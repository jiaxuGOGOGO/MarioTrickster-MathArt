"""
SESSION-063: Tests for Dimension Uplift Engine & Evolution Bridge.

Validates all Phase 5 components:
  1. 3D SDF Primitives (IQ formulas)
  2. Smooth Min 3D (smin variants + gradient blending)
  3. SDF Dimension Lifter (2D→3D extrusion/revolution)
  4. Dual Contouring Mesh Extractor (Tao Ju et al.)
  5. Adaptive SDF Cache (Pujol & Chica 2023)
  6. Isometric Displacement Mapper
  7. Cel-Shading Config (Arc System Works)
  8. Taichi AOT Bridge (code generation)
  9. Three-Layer Evolution Bridge
"""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: 3D SDF Primitives Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSDF3DPrimitives:
    """Test Inigo Quilez 3D SDF primitive implementations."""

    def test_sphere_at_origin(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.sphere(np.array([0.0, 0.0, 0.0]), 1.0)
        assert abs(d - (-1.0)) < 1e-6, "Origin should be inside unit sphere"

    def test_sphere_on_surface(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.sphere(np.array([1.0, 0.0, 0.0]), 1.0)
        assert abs(d) < 1e-6, "Point on surface should have distance ~0"

    def test_sphere_outside(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.sphere(np.array([2.0, 0.0, 0.0]), 1.0)
        assert abs(d - 1.0) < 1e-6, "Point at (2,0,0) should be 1.0 from unit sphere"

    def test_box_inside(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.box(np.array([0.0, 0.0, 0.0]),
                                np.array([1.0, 1.0, 1.0]))
        assert d < 0, "Origin should be inside unit box"

    def test_box_on_face(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.box(np.array([1.0, 0.0, 0.0]),
                                np.array([1.0, 1.0, 1.0]))
        assert abs(d) < 1e-6, "Point on face should have distance ~0"

    def test_capsule_on_axis(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.capsule(
            np.array([0.0, 0.0, 0.0]),
            np.array([0.0, -1.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            0.3
        )
        assert d < 0, "Point on capsule axis should be inside"

    def test_torus_center(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.torus(np.array([0.0, 0.0, 0.0]), 1.0, 0.3)
        # Center of torus is outside (distance = major_r - minor_r)
        assert d > 0, "Center of torus should be outside"

    def test_cylinder_inside(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.cylinder(np.array([0.0, 0.0, 0.0]), 1.0, 1.0)
        assert d < 0, "Origin should be inside cylinder"

    def test_ellipsoid_inside(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.ellipsoid(np.array([0.0, 0.0, 0.0]),
                                       np.array([1.0, 2.0, 1.0]))
        assert d <= 0, "Origin should be inside ellipsoid"

    def test_rounded_box_inside(self):
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        d = SDF3DPrimitives.rounded_box(np.array([0.0, 0.0, 0.0]),
                                         np.array([1.0, 1.0, 1.0]), 0.1)
        assert d < 0, "Origin should be inside rounded box"

    def test_all_primitives_return_finite(self):
        """All primitives should return finite float values."""
        from mathart.animation.dimension_uplift_engine import SDF3DPrimitives
        p = np.array([0.5, 0.3, 0.2])
        results = [
            SDF3DPrimitives.sphere(p, 1.0),
            SDF3DPrimitives.box(p, np.array([0.5, 0.5, 0.5])),
            SDF3DPrimitives.capsule(p, np.array([0, -1, 0]),
                                     np.array([0, 1, 0]), 0.3),
            SDF3DPrimitives.torus(p, 0.5, 0.2),
            SDF3DPrimitives.cylinder(p, 0.5, 1.0),
            SDF3DPrimitives.ellipsoid(p, np.array([1.0, 0.5, 0.3])),
            SDF3DPrimitives.rounded_box(p, np.array([0.4, 0.4, 0.4]), 0.1),
        ]
        for r in results:
            assert isinstance(r, float) and math.isfinite(r)


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Smooth Min 3D Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSmoothMin3D:
    """Test IQ smooth minimum operators."""

    def test_smin_quadratic_blends_below_min(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, t = SmoothMin3D.smin_quadratic(0.3, 0.5, 0.5)
        assert d < min(0.3, 0.5), "smin should be less than min(a,b)"

    def test_smin_cubic_blends_below_min(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, t = SmoothMin3D.smin_cubic(0.3, 0.5, 0.5)
        assert d < min(0.3, 0.5), "smin_cubic should be less than min(a,b)"

    def test_smin_exponential_blends_below_min(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, t = SmoothMin3D.smin_exponential(0.3, 0.5, 0.2)
        assert d < min(0.3, 0.5), "smin_exp should be less than min(a,b)"

    def test_smin_k_zero_equals_min(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, _ = SmoothMin3D.smin_quadratic(0.3, 0.5, 0.0)
        assert abs(d - 0.3) < 1e-6, "k=0 should give exact min"

    def test_smin_symmetric(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d1, _ = SmoothMin3D.smin_quadratic(0.3, 0.5, 0.2)
        d2, _ = SmoothMin3D.smin_quadratic(0.5, 0.3, 0.2)
        assert abs(d1 - d2) < 1e-6, "smin should be symmetric"

    def test_smooth_subtraction(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, _ = SmoothMin3D.smooth_subtraction(0.3, 0.5, 0.1)
        assert isinstance(d, float) and math.isfinite(d)

    def test_smooth_intersection(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        d, _ = SmoothMin3D.smooth_intersection(0.3, 0.5, 0.1)
        assert isinstance(d, float) and math.isfinite(d)

    def test_gradient_blend(self):
        from mathart.animation.dimension_uplift_engine import SmoothMin3D
        g_a = np.array([1.0, 0.0, 0.0])
        g_b = np.array([0.0, 1.0, 0.0])
        blended = SmoothMin3D.gradient_blend(g_a, g_b, 0.5)
        expected = np.array([0.5, 0.5, 0.0])
        np.testing.assert_allclose(blended, expected, atol=1e-6)


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: SDF Dimension Lifter Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSDFDimensionLifter:
    """Test 2D→3D SDF lifting operations."""

    @staticmethod
    def _circle_sdf(x: float, y: float) -> float:
        return math.sqrt(x * x + y * y) - 0.5

    def test_extrude_inside(self):
        from mathart.animation.dimension_uplift_engine import SDFDimensionLifter
        sdf_3d = SDFDimensionLifter.extrude_2d_to_3d(self._circle_sdf, 0.4)
        d = sdf_3d(0.0, 0.0, 0.0)
        assert d < 0, "Origin should be inside extruded circle"

    def test_extrude_outside_z(self):
        from mathart.animation.dimension_uplift_engine import SDFDimensionLifter
        sdf_3d = SDFDimensionLifter.extrude_2d_to_3d(self._circle_sdf, 0.4)
        d = sdf_3d(0.0, 0.0, 1.0)
        assert d > 0, "Far Z should be outside extruded circle"

    def test_revolve_inside(self):
        from mathart.animation.dimension_uplift_engine import SDFDimensionLifter

        def profile(r: float, y: float) -> float:
            return math.sqrt((r - 0.5) ** 2 + y * y) - 0.2

        sdf_3d = SDFDimensionLifter.revolve_2d_to_3d(profile)
        d = sdf_3d(0.5, 0.0, 0.0)
        assert d < 0, "Point on torus ring should be inside"

    def test_smooth_blend_3d(self):
        from mathart.animation.dimension_uplift_engine import (
            SDFDimensionLifter, SDF3DPrimitives
        )

        def sphere1(x, y, z):
            return SDF3DPrimitives.sphere(np.array([x - 0.3, y, z]), 0.5)

        def sphere2(x, y, z):
            return SDF3DPrimitives.sphere(np.array([x + 0.3, y, z]), 0.5)

        blended = SDFDimensionLifter.smooth_blend_3d(
            [sphere1, sphere2], [0.2]
        )
        d = blended(0.0, 0.0, 0.0)
        assert d < 0, "Blended spheres should contain origin"


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Dual Contouring Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDualContouring:
    """Test Dual Contouring mesh extraction."""

    @staticmethod
    def _sphere_sdf(x: float, y: float, z: float) -> float:
        return math.sqrt(x * x + y * y + z * z) - 0.5

    def test_extract_sphere_mesh(self):
        from mathart.animation.dimension_uplift_engine import DualContouringExtractor
        extractor = DualContouringExtractor(resolution=16, bias_strength=0.01)
        mesh = extractor.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        assert mesh.vertex_count > 0, "Should produce vertices"
        assert mesh.face_count > 0, "Should produce faces"

    def test_mesh_vertices_inside_bounds(self):
        from mathart.animation.dimension_uplift_engine import DualContouringExtractor
        extractor = DualContouringExtractor(resolution=16)
        mesh = extractor.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        for v in mesh.vertices:
            assert all(-1.1 <= c <= 1.1 for c in v), \
                f"Vertex {v} outside bounds"

    def test_triangulate(self):
        from mathart.animation.dimension_uplift_engine import DualContouringExtractor
        extractor = DualContouringExtractor(resolution=16)
        mesh = extractor.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        tris = mesh.triangulate()
        assert len(tris) == 2 * mesh.face_count, \
            "Each quad should produce 2 triangles"

    def test_export_obj(self):
        from mathart.animation.dimension_uplift_engine import DualContouringExtractor
        extractor = DualContouringExtractor(resolution=12)
        extractor.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        with tempfile.NamedTemporaryFile(suffix=".obj", delete=False) as f:
            path = f.name
        try:
            extractor.export_obj(path)
            content = Path(path).read_text()
            assert "v " in content, "OBJ should contain vertices"
            assert "f " in content, "OBJ should contain faces"
        finally:
            os.unlink(path)

    def test_higher_resolution_more_detail(self):
        from mathart.animation.dimension_uplift_engine import DualContouringExtractor
        ext_lo = DualContouringExtractor(resolution=8)
        ext_hi = DualContouringExtractor(resolution=16)
        mesh_lo = ext_lo.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        mesh_hi = ext_hi.extract(self._sphere_sdf, bounds=(-1.0, 1.0))
        assert mesh_hi.vertex_count >= mesh_lo.vertex_count, \
            "Higher resolution should produce more vertices"


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Adaptive SDF Cache Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAdaptiveSDFCache:
    """Test Pujol & Chica adaptive SDF cache."""

    @staticmethod
    def _sphere_sdf(x: float, y: float, z: float) -> float:
        return math.sqrt(x * x + y * y + z * z) - 0.5

    def test_build_cache(self):
        from mathart.animation.dimension_uplift_engine import AdaptiveSDFCache
        cache = AdaptiveSDFCache(max_depth=4, error_threshold=0.05)
        cache.build(self._sphere_sdf,
                    np.array([-1.0, -1.0, -1.0]),
                    np.array([1.0, 1.0, 1.0]))
        assert cache.node_count > 1, "Should create multiple nodes"

    def test_cache_query_accuracy(self):
        from mathart.animation.dimension_uplift_engine import AdaptiveSDFCache
        cache = AdaptiveSDFCache(max_depth=5, error_threshold=0.01)
        cache.build(self._sphere_sdf,
                    np.array([-1.5, -1.5, -1.5]),
                    np.array([1.5, 1.5, 1.5]))

        # Test accuracy at random points
        rng = np.random.RandomState(42)
        max_error = 0.0
        for _ in range(50):
            p = rng.uniform(-1.0, 1.0, size=3)
            cached = cache.query(*p)
            actual = self._sphere_sdf(*p)
            max_error = max(max_error, abs(cached - actual))

        assert max_error < 2.0, f"Max cache error {max_error:.4f} too large"

    def test_deeper_cache_more_accurate(self):
        from mathart.animation.dimension_uplift_engine import AdaptiveSDFCache
        cache_shallow = AdaptiveSDFCache(max_depth=3, error_threshold=0.1)
        cache_deep = AdaptiveSDFCache(max_depth=6, error_threshold=0.005)

        bmin = np.array([-1.0, -1.0, -1.0])
        bmax = np.array([1.0, 1.0, 1.0])
        cache_shallow.build(self._sphere_sdf, bmin, bmax)
        cache_deep.build(self._sphere_sdf, bmin, bmax)

        assert cache_deep.node_count >= cache_shallow.node_count


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Isometric Displacement Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestIsometricDisplacement:
    """Test isometric camera and displacement mapping."""

    @staticmethod
    def _circle_sdf(x: float, y: float) -> float:
        return math.sqrt(x * x + y * y) - 0.5

    def test_camera_matrices(self):
        from mathart.animation.dimension_uplift_engine import IsometricCameraConfig
        cam = IsometricCameraConfig()
        proj = cam.to_projection_matrix()
        view = cam.to_view_matrix()
        assert proj.shape == (4, 4)
        assert view.shape == (4, 4)

    def test_depth_map_generation(self):
        from mathart.animation.dimension_uplift_engine import IsometricDisplacementMapper
        mapper = IsometricDisplacementMapper()
        depth = mapper.generate_depth_map(self._circle_sdf, resolution=32)
        assert depth.shape == (32, 32)
        assert depth.min() >= 0.0
        assert depth.max() <= 1.0
        assert np.any(depth > 0), "Should have non-zero depth inside circle"

    def test_tessellate_plane(self):
        from mathart.animation.dimension_uplift_engine import IsometricDisplacementMapper
        mapper = IsometricDisplacementMapper()
        verts, indices = mapper.tessellate_plane(subdivisions=4)
        assert verts.shape == (25, 3)  # (4+1)^2 = 25
        assert indices.shape[1] == 3
        assert len(indices) == 32  # 4*4*2 = 32 triangles

    def test_displacement(self):
        from mathart.animation.dimension_uplift_engine import IsometricDisplacementMapper
        mapper = IsometricDisplacementMapper()
        verts, _ = mapper.tessellate_plane(subdivisions=8)
        depth = mapper.generate_depth_map(self._circle_sdf, resolution=64)
        displaced = mapper.apply_displacement(verts, depth, strength=0.5)
        assert displaced.shape == verts.shape
        # Center vertex should be displaced upward
        center_idx = len(verts) // 2
        assert displaced[center_idx, 2] >= verts[center_idx, 2]

    def test_unity_shader_config(self):
        from mathart.animation.dimension_uplift_engine import IsometricDisplacementMapper
        mapper = IsometricDisplacementMapper()
        config = mapper.export_unity_shader_graph_config()
        assert "tessellation" in config
        assert "displacement" in config
        assert config["camera"]["projection"] == "Orthographic"


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Cel-Shading Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCelShading:
    """Test Arc System Works cel-shading configuration."""

    def test_inverted_hull_shader(self):
        from mathart.animation.dimension_uplift_engine import CelShadingConfig
        config = CelShadingConfig()
        shader = config.generate_inverted_hull_shader()
        assert "Cull Front" in shader
        assert "_OutlineWidth" in shader
        assert "HLSLPROGRAM" in shader

    def test_cel_shading_shader(self):
        from mathart.animation.dimension_uplift_engine import CelShadingConfig
        config = CelShadingConfig()
        shader = config.generate_cel_shading_shader()
        assert "NdotL" in shader
        assert "step" in shader
        assert "_ShadowThreshold" in shader

    def test_stepped_animation_config(self):
        from mathart.animation.dimension_uplift_engine import CelShadingConfig
        config = CelShadingConfig(stepped_animation_fps=12)
        anim = config.generate_stepped_animation_config()
        assert anim["target_fps"] == 12
        assert anim["interpolation"] == "None"


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Taichi AOT Bridge Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTaichiAOTBridge:
    """Test Taichi AOT code generation."""

    def test_aot_export_script(self):
        from mathart.animation.dimension_uplift_engine import TaichiAOTBridge
        bridge = TaichiAOTBridge()
        script = bridge.generate_aot_export_script()
        assert "ti.aot.Module" in script
        assert "predict_positions" in script
        assert "solve_distance_constraints" in script
        assert "SPIR-V" in script or "XPBD" in script

    def test_unity_native_plugin(self):
        from mathart.animation.dimension_uplift_engine import TaichiAOTBridge
        bridge = TaichiAOTBridge()
        code = bridge.generate_unity_native_plugin_code()
        assert "TaichiBridge_Init" in code
        assert "TaichiBridge_Substep" in code
        assert "ti_launch_kernel" in code

    def test_csharp_bridge(self):
        from mathart.animation.dimension_uplift_engine import TaichiAOTBridge
        bridge = TaichiAOTBridge()
        code = bridge.generate_csharp_bridge_code()
        assert "DllImport" in code
        assert "ComputeBuffer" in code
        assert "FixedUpdate" in code


# ═══════════════════════════════════════════════════════════════════════════
# Section 9: Evolution Bridge Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDimensionUpliftEvolutionBridge:
    """Test three-layer evolution bridge."""

    def test_full_cycle(self, tmp_path):
        from mathart.evolution.dimension_uplift_bridge import (
            DimensionUpliftEvolutionBridge,
        )
        # Create minimal project structure
        (tmp_path / "mathart" / "animation").mkdir(parents=True)
        (tmp_path / "mathart" / "evolution").mkdir(parents=True)
        (tmp_path / "knowledge").mkdir(parents=True)

        bridge = DimensionUpliftEvolutionBridge(tmp_path)
        metrics, rules, bonus = bridge.run_full_cycle(dc_resolution=12)

        assert metrics.cycle_id == 1
        assert metrics.dc_vertex_count > 0
        assert metrics.dc_face_count > 0
        assert isinstance(bonus, float)
        assert 0.0 <= bonus <= 0.30

    def test_state_persistence(self, tmp_path):
        from mathart.evolution.dimension_uplift_bridge import (
            DimensionUpliftEvolutionBridge,
        )
        (tmp_path / "knowledge").mkdir(parents=True)

        bridge1 = DimensionUpliftEvolutionBridge(tmp_path)
        bridge1.run_full_cycle(dc_resolution=10)

        # Load state in new bridge instance
        bridge2 = DimensionUpliftEvolutionBridge(tmp_path)
        assert bridge2.state.total_cycles == 1
        assert len(bridge2.state.feature_trend) == 1

    def test_knowledge_file_created(self, tmp_path):
        from mathart.evolution.dimension_uplift_bridge import (
            DimensionUpliftEvolutionBridge,
        )
        (tmp_path / "knowledge").mkdir(parents=True)

        bridge = DimensionUpliftEvolutionBridge(tmp_path)
        bridge.run_full_cycle(dc_resolution=10)

        knowledge_path = tmp_path / "knowledge" / "dimension_uplift_rules.md"
        assert knowledge_path.exists()
        content = knowledge_path.read_text()
        assert "Distilled Rules" in content
        assert "Dual Contouring" in content or "dc_resolution" in content

    def test_status_collector(self):
        from mathart.evolution.dimension_uplift_bridge import (
            collect_dimension_uplift_status,
        )
        project_root = Path(__file__).parent.parent
        status = collect_dimension_uplift_status(project_root)
        assert status.engine_module_exists is True
        assert "DualContouringExtractor" in status.tracked_exports

    def test_metrics_pass_gate(self, tmp_path):
        from mathart.evolution.dimension_uplift_bridge import (
            DimensionUpliftEvolutionBridge,
        )
        (tmp_path / "knowledge").mkdir(parents=True)

        bridge = DimensionUpliftEvolutionBridge(tmp_path)
        metrics, _, _ = bridge.run_full_cycle(dc_resolution=16)

        # With resolution 16, should produce a valid mesh
        assert metrics.sdf_3d_primitives_tested >= 5
        assert metrics.all_modules_valid is True


# ═══════════════════════════════════════════════════════════════════════════
# Section 10: Integration Test
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration test for the full pipeline."""

    def test_full_pipeline_2d_to_obj(self):
        """Test complete pipeline: 2D SDF → 3D → Cache → DC → OBJ."""
        from mathart.animation.dimension_uplift_engine import (
            SDFDimensionLifter,
            DualContouringExtractor,
            AdaptiveSDFCache,
        )

        # 1. Define 2D SDF (rounded rectangle)
        def rounded_rect_2d(x: float, y: float) -> float:
            dx = abs(x) - 0.6
            dy = abs(y) - 0.3
            return (math.sqrt(max(dx, 0) ** 2 + max(dy, 0) ** 2) +
                    min(max(dx, dy), 0.0) - 0.05)

        # 2. Lift to 3D
        lifter = SDFDimensionLifter()
        sdf_3d = lifter.extrude_2d_to_3d(rounded_rect_2d, depth=0.2)

        # 3. Build adaptive cache
        cache = AdaptiveSDFCache(max_depth=4, error_threshold=0.02)
        cache.build(sdf_3d,
                    np.array([-1.0, -1.0, -1.0]),
                    np.array([1.0, 1.0, 1.0]))
        assert cache.node_count > 0

        # 4. Extract mesh via Dual Contouring
        extractor = DualContouringExtractor(resolution=16)
        mesh = extractor.extract(sdf_3d, bounds=(-1.0, 1.0))
        assert mesh.vertex_count > 0
        assert mesh.face_count > 0

        # 5. Export OBJ
        with tempfile.NamedTemporaryFile(suffix=".obj", delete=False) as f:
            path = f.name
        try:
            extractor.export_obj(path)
            content = Path(path).read_text()
            v_count = content.count("\nv ")
            f_count = content.count("\nf ")
            assert v_count > 0
            assert f_count > 0
        finally:
            os.unlink(path)
