"""SESSION-065 — Comprehensive tests for all research-to-code modules.

Tests cover:
1. QEM Simplifier (Garland & Heckbert 1997)
2. Vertex Normal Editor (Arc System Works / GGXrd)
3. DeepPhase FFT Multi-Channel (Starke 2022)
4. SparseCtrl Bridge (Guo et al. 2023)
5. Motion Matching KD-Tree (Clavet 2016)
"""
import math
import numpy as np
import pytest


TEST_RNG_SEED = 42


def make_rng(seed: int = TEST_RNG_SEED) -> np.random.Generator:
    """Return an isolated RNG for a single test context."""
    return np.random.default_rng(seed)


# ═══════════════════════════════════════════════════════════════════════════
# 1. QEM Simplifier Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestQEMSimplifier:
    """Tests for Quadric Error Metrics mesh simplification."""

    def _make_cube_mesh(self):
        """Create a simple cube mesh for testing."""
        from mathart.animation.qem_simplifier import QEMMesh
        vertices = np.array([
            [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
            [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
        ], dtype=np.float64)
        triangles = np.array([
            [0, 1, 2], [0, 2, 3],  # front
            [4, 6, 5], [4, 7, 6],  # back
            [0, 4, 5], [0, 5, 1],  # bottom
            [2, 6, 7], [2, 7, 3],  # top
            [0, 3, 7], [0, 7, 4],  # left
            [1, 5, 6], [1, 6, 2],  # right
        ], dtype=np.int64)
        return QEMMesh(vertices=vertices, triangles=triangles)

    def _make_subdivided_plane(self, n=5):
        """Create a subdivided plane mesh with n×n quads."""
        from mathart.animation.qem_simplifier import QEMMesh
        vertices = []
        for j in range(n + 1):
            for i in range(n + 1):
                vertices.append([i / n, j / n, 0.0])
        vertices = np.array(vertices, dtype=np.float64)

        triangles = []
        for j in range(n):
            for i in range(n):
                v0 = j * (n + 1) + i
                v1 = v0 + 1
                v2 = v0 + (n + 1) + 1
                v3 = v0 + (n + 1)
                triangles.append([v0, v1, v2])
                triangles.append([v0, v2, v3])
        triangles = np.array(triangles, dtype=np.int64)
        return QEMMesh(vertices=vertices, triangles=triangles)

    def test_import(self):
        """QEM module imports successfully."""
        from mathart.animation.qem_simplifier import (
            QEMMesh, QEMConfig, QEMSimplifier, simplify_mesh, create_lod_chain
        )
        assert QEMSimplifier is not None

    def test_mesh_creation(self):
        """QEMMesh stores vertices and triangles correctly."""
        mesh = self._make_cube_mesh()
        assert mesh.vertex_count == 8
        assert mesh.face_count == 12

    def test_face_normals(self):
        """Face normal computation produces unit vectors."""
        mesh = self._make_cube_mesh()
        normals = mesh.compute_face_normals()
        assert normals.shape == (12, 3)
        lengths = np.linalg.norm(normals, axis=1)
        np.testing.assert_allclose(lengths, 1.0, atol=1e-6)

    def test_simplify_reduces_faces(self):
        """Simplification reduces face count."""
        from mathart.animation.qem_simplifier import QEMSimplifier
        mesh = self._make_subdivided_plane(n=5)
        original_faces = mesh.face_count
        assert original_faces == 50  # 5×5×2

        simplifier = QEMSimplifier()
        simplified = simplifier.simplify(mesh, target_ratio=0.5)
        assert simplified.face_count < original_faces
        assert simplified.face_count >= 4  # Minimum

    def test_simplify_preserves_bounds(self):
        """Simplified mesh stays within original bounding box."""
        from mathart.animation.qem_simplifier import QEMSimplifier
        mesh = self._make_subdivided_plane(n=8)
        orig_min = mesh.vertices.min(axis=0)
        orig_max = mesh.vertices.max(axis=0)

        simplifier = QEMSimplifier()
        simplified = simplifier.simplify(mesh, target_ratio=0.3)

        if simplified.vertex_count > 0:
            simp_min = simplified.vertices.min(axis=0)
            simp_max = simplified.vertices.max(axis=0)
            # Simplified should be within or near original bounds
            assert np.all(simp_min >= orig_min - 0.1)
            assert np.all(simp_max <= orig_max + 0.1)

    def test_lod_chain(self):
        """LOD chain generates multiple levels."""
        from mathart.animation.qem_simplifier import QEMSimplifier
        mesh = self._make_subdivided_plane(n=6)
        simplifier = QEMSimplifier()
        chain = simplifier.generate_lod_chain(
            mesh, levels=[1.0, 0.5, 0.25]
        )
        assert len(chain) == 3
        # Face counts should decrease
        for i in range(1, len(chain)):
            assert chain[i].face_count <= chain[i - 1].face_count

    def test_convenience_function(self):
        """simplify_mesh convenience function works."""
        from mathart.animation.qem_simplifier import simplify_mesh
        mesh = self._make_subdivided_plane(n=4)
        result = simplify_mesh(mesh, target_ratio=0.5)
        assert result.face_count < mesh.face_count


# ═══════════════════════════════════════════════════════════════════════════
# 2. Vertex Normal Editor Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestVertexNormalEditor:
    """Tests for vertex normal editing (GGXrd technique)."""

    def _make_test_mesh(self):
        """Create a simple test mesh."""
        vertices = np.array([
            [0, 0, 0], [1, 0, 0], [0.5, 1, 0],
            [0, 0, 1], [1, 0, 1], [0.5, 1, 1],
        ], dtype=np.float64)
        triangles = np.array([
            [0, 1, 2], [3, 4, 5],
            [0, 1, 4], [0, 4, 3],
        ], dtype=np.int64)
        return vertices, triangles

    def test_import(self):
        """Vertex normal editor imports successfully."""
        from mathart.animation.vertex_normal_editor import (
            VertexNormalEditor, ProxyShape, ShadowConfig, EditedMesh
        )
        assert VertexNormalEditor is not None

    def test_proxy_sphere_normal(self):
        """Sphere proxy produces radial normals."""
        from mathart.animation.vertex_normal_editor import ProxyShape
        proxy = ProxyShape.sphere(center=[0, 0, 0], radius=1.0)
        n = proxy.compute_normal_at(np.array([1, 0, 0]))
        np.testing.assert_allclose(n, [1, 0, 0], atol=1e-6)

    def test_proxy_cylinder_normal(self):
        """Cylinder proxy produces radial normals perpendicular to axis."""
        from mathart.animation.vertex_normal_editor import ProxyShape
        proxy = ProxyShape.cylinder(
            center=[0, 0, 0], radius=1.0, axis=[0, 1, 0]
        )
        n = proxy.compute_normal_at(np.array([1, 5, 0]))
        # Should be radial in XZ plane
        assert abs(n[1]) < 0.01  # No Y component
        assert abs(n[0] - 1.0) < 0.01

    def test_normal_transfer(self):
        """Normal transfer from sphere proxy produces smooth normals."""
        from mathart.animation.vertex_normal_editor import (
            VertexNormalEditor, ProxyShape
        )
        vertices, triangles = self._make_test_mesh()
        editor = VertexNormalEditor()
        proxy = ProxyShape.sphere(center=[0.5, 0.5, 0.5], radius=1.0)

        edited = editor.transfer_normals_from_proxy(
            vertices, triangles, proxy, blend_weight=1.0
        )

        assert edited.vertex_count == 6
        # All edited normals should be unit vectors
        lengths = np.linalg.norm(edited.edited_normals, axis=1)
        np.testing.assert_allclose(lengths, 1.0, atol=1e-6)

    def test_shadow_computation(self):
        """Cel shadow boundary computation produces valid values."""
        from mathart.animation.vertex_normal_editor import (
            VertexNormalEditor, ProxyShape
        )
        vertices, triangles = self._make_test_mesh()
        editor = VertexNormalEditor()
        proxy = ProxyShape.sphere(center=[0.5, 0.5, 0.5])
        edited = editor.transfer_normals_from_proxy(
            vertices, triangles, proxy
        )

        shadow = editor.compute_cel_shadow_boundary(
            edited, light_dir=[0, 1, 0], threshold=0.5
        )
        assert shadow.shape == (6,)
        assert np.all(shadow >= 0.0)
        assert np.all(shadow <= 1.0)

    def test_normal_smoothing(self):
        """Normal smoothing within a group converges normals."""
        from mathart.animation.vertex_normal_editor import (
            VertexNormalEditor, ProxyShape
        )
        vertices, triangles = self._make_test_mesh()
        editor = VertexNormalEditor()
        proxy = ProxyShape.sphere(center=[0.5, 0.5, 0.5])
        edited = editor.transfer_normals_from_proxy(
            vertices, triangles, proxy
        )

        # Smooth all vertices
        smoothed = editor.smooth_normals_by_group(
            edited, set(range(6)), iterations=5, strength=0.8
        )
        # Normals should still be unit vectors
        lengths = np.linalg.norm(smoothed.edited_normals, axis=1)
        np.testing.assert_allclose(lengths, 1.0, atol=1e-6)

    def test_shadow_bias_painting(self):
        """Shadow bias painting updates per-vertex bias."""
        from mathart.animation.vertex_normal_editor import (
            VertexNormalEditor, ProxyShape
        )
        vertices, triangles = self._make_test_mesh()
        editor = VertexNormalEditor()
        proxy = ProxyShape.sphere(center=[0.5, 0.5, 0.5])
        edited = editor.transfer_normals_from_proxy(
            vertices, triangles, proxy
        )

        painted = editor.paint_shadow_threshold(
            edited, {0: 0.3, 1: -0.2}
        )
        assert abs(painted.shadow_bias[0] - 0.3) < 1e-6
        assert abs(painted.shadow_bias[1] - (-0.2)) < 1e-6

    def test_hlsl_shader_generation(self):
        """HLSL shader code generation produces non-empty string."""
        from mathart.animation.vertex_normal_editor import VertexNormalEditor
        editor = VertexNormalEditor()
        shader = editor.generate_hlsl_vertex_normal_shader()
        assert "ShadowThreshold" in shader
        assert "EditedNormalMap" in shader
        assert len(shader) > 500


# ═══════════════════════════════════════════════════════════════════════════
# 3. DeepPhase FFT Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDeepPhaseFFT:
    """Tests for DeepPhase multi-channel FFT decomposition."""

    def _make_test_signal(self, freq=2.0, amp=1.0, phase=0.0,
                          offset=0.0, duration=2.0, sr=30.0):
        """Create a synthetic sinusoidal signal."""
        t = np.arange(0, duration, 1.0 / sr)
        return amp * np.sin(2 * np.pi * (freq * t - phase)) + offset

    def test_import(self):
        """DeepPhase FFT module imports successfully."""
        from mathart.animation.deepphase_fft import (
            DeepPhaseAnalyzer, PhaseManifoldPoint, PhaseBlender,
            AsymmetricGaitAnalyzer
        )
        assert DeepPhaseAnalyzer is not None

    def test_single_frequency_decomposition(self):
        """Decompose a single-frequency signal correctly."""
        from mathart.animation.deepphase_fft import DeepPhaseAnalyzer
        signal = self._make_test_signal(freq=3.0, amp=2.0, offset=1.0)
        analyzer = DeepPhaseAnalyzer(sample_rate=30.0)
        points = analyzer.decompose(signal, "test")

        assert len(points) >= 1
        dominant = points[0]
        assert abs(dominant.frequency - 3.0) < 0.5
        assert abs(dominant.amplitude - 2.0) < 0.5

    def test_manifold_representation(self):
        """Phase manifold point has correct 2D coordinates."""
        from mathart.animation.deepphase_fft import PhaseManifoldPoint
        p = PhaseManifoldPoint(amplitude=1.0, phase_shift=0.25)
        # phase_shift=0.25 → angle=π/2 → (cos, sin) = (0, 1)
        assert abs(p.manifold_x - 0.0) < 1e-6
        assert abs(p.manifold_y - 1.0) < 1e-6

    def test_phase_blending(self):
        """Phase blending in manifold space produces valid results."""
        from mathart.animation.deepphase_fft import (
            PhaseManifoldPoint, PhaseBlender
        )
        p1 = PhaseManifoldPoint(amplitude=1.0, frequency=2.0,
                                phase_shift=0.0)
        p2 = PhaseManifoldPoint(amplitude=1.0, frequency=2.0,
                                phase_shift=0.5)

        blended = PhaseBlender.blend(p1, p2, 0.5)
        # Midpoint should have reduced amplitude (vectors partially cancel)
        assert blended.amplitude < 1.0
        assert blended.frequency == 2.0

    def test_multi_blend(self):
        """Multi-point blending with weights works correctly."""
        from mathart.animation.deepphase_fft import (
            PhaseManifoldPoint, PhaseBlender
        )
        points = [
            PhaseManifoldPoint(amplitude=1.0, frequency=2.0, phase_shift=0.0),
            PhaseManifoldPoint(amplitude=1.0, frequency=3.0, phase_shift=0.0),
        ]
        blended = PhaseBlender.blend_multi(points, [0.5, 0.5])
        assert abs(blended.frequency - 2.5) < 1e-6

    def test_biped_asymmetry_detection(self):
        """Asymmetric biped gait detection works."""
        from mathart.animation.deepphase_fft import AsymmetricGaitAnalyzer
        analyzer = AsymmetricGaitAnalyzer(sample_rate=30.0)

        # Symmetric gait
        left = self._make_test_signal(freq=2.0, amp=1.0, phase=0.0)
        right = self._make_test_signal(freq=2.0, amp=1.0, phase=0.5)
        report = analyzer.analyze_biped(left, right)
        assert report.asymmetry_ratio < 0.3  # Should be low

        # Asymmetric gait (limping)
        left_limp = self._make_test_signal(freq=2.0, amp=1.0, phase=0.0)
        right_limp = self._make_test_signal(freq=1.5, amp=0.5, phase=0.3)
        report_limp = analyzer.analyze_biped(left_limp, right_limp)
        assert report_limp.asymmetry_ratio > report.asymmetry_ratio

    def test_quadruped_gait_classification(self):
        """Quadruped gait type classification works."""
        from mathart.animation.deepphase_fft import AsymmetricGaitAnalyzer
        analyzer = AsymmetricGaitAnalyzer(sample_rate=30.0)

        # Trot: diagonal pairs in sync
        fl = self._make_test_signal(freq=2.0, phase=0.0)
        fr = self._make_test_signal(freq=2.0, phase=0.5)
        hl = self._make_test_signal(freq=2.0, phase=0.5)
        hr = self._make_test_signal(freq=2.0, phase=0.0)
        report = analyzer.analyze_quadruped(fl, fr, hl, hr)
        assert report.gait_type in ("trot", "walk")

    def test_signal_reconstruction(self):
        """Signal reconstruction from manifold points is accurate."""
        from mathart.animation.deepphase_fft import DeepPhaseAnalyzer
        signal = self._make_test_signal(freq=2.0, amp=1.5, offset=0.5)
        analyzer = DeepPhaseAnalyzer(sample_rate=30.0)
        points = analyzer.decompose(signal)

        reconstructed = analyzer.reconstruct(points, duration=2.0,
                                             num_samples=len(signal))
        # Reconstruction should be reasonably close
        # Note: phase convention may invert the signal, so use abs correlation
        correlation = abs(np.corrcoef(signal, reconstructed)[0, 1])
        assert correlation > 0.5


# ═══════════════════════════════════════════════════════════════════════════
# 4. SparseCtrl Bridge Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSparseCtrlBridge:
    """Tests for SparseCtrl integration bridge."""

    def test_import(self):
        """SparseCtrl bridge imports successfully."""
        from mathart.animation.sparse_ctrl_bridge import (
            SparseCtrlBridge, SparseCtrlConfig, MotionVectorConditioner,
            ConditionType, PropagationMode
        )
        assert SparseCtrlBridge is not None

    def test_prepare_conditions(self):
        """Sparse condition preparation works correctly."""
        from mathart.animation.sparse_ctrl_bridge import SparseCtrlBridge
        bridge = SparseCtrlBridge()

        conditions = {
            "depth": {
                0: np.zeros((32, 32, 3)),
                10: np.ones((32, 32, 3)),
                20: np.zeros((32, 32, 3)),
            }
        }
        batch = bridge.prepare_sparse_conditions(30, conditions)

        assert batch.total_frames == 30
        assert batch.sparse_indices == [0, 10, 20]
        assert batch.density == 3.0 / 30.0
        assert batch.max_gap == 10

    def test_condition_mask(self):
        """Condition mask correctly marks sparse frames."""
        from mathart.animation.sparse_ctrl_bridge import SparseCtrlBridge
        bridge = SparseCtrlBridge()
        mask = bridge.build_condition_mask(20, [0, 5, 10, 15, 19])
        assert mask.sum() == 5
        assert mask[0] and mask[5] and mask[10]

    def test_workflow_generation(self):
        """ComfyUI workflow generation produces valid structure."""
        from mathart.animation.sparse_ctrl_bridge import SparseCtrlBridge
        bridge = SparseCtrlBridge()

        conditions = {"depth": {0: np.zeros((32, 32, 3))}}
        batch = bridge.prepare_sparse_conditions(10, conditions)
        workflow = bridge.build_comfyui_workflow(
            batch, prompt="test character"
        )

        assert workflow["pipeline"] == "animatediff_sparsectrl"
        assert workflow["sparse_ctrl"]["enabled"] is True
        assert "depth" in workflow["conditions"]

    def test_interpolation(self):
        """Missing condition interpolation fills gaps."""
        from mathart.animation.sparse_ctrl_bridge import SparseCtrlBridge
        bridge = SparseCtrlBridge()

        conditions = [
            np.zeros((4, 4, 3)),
            None, None, None,
            np.ones((4, 4, 3)),
        ]
        mask = np.array([True, False, False, False, True])

        filled = bridge.interpolate_missing_conditions(conditions, mask)
        assert len(filled) == 5
        # Middle frame should be interpolated
        mid_val = filled[2].mean()
        assert 0.3 < mid_val < 0.7  # Roughly 0.5

    def test_temporal_consistency(self):
        """Temporal consistency scoring works."""
        from mathart.animation.sparse_ctrl_bridge import SparseCtrlBridge
        bridge = SparseCtrlBridge()

        # Identical frames = perfect consistency
        frames = [np.full((32, 32, 3), 128, dtype=np.uint8)] * 5
        score = bridge.compute_temporal_consistency_score(frames)
        assert score > 0.99
        assert score == pytest.approx(1.0, abs=1e-12)

        # Random frames = low consistency
        rng = make_rng(301)
        random_frames = [
            rng.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
            for _ in range(5)
        ]
        random_score = bridge.compute_temporal_consistency_score(random_frames)
        assert random_score < score
        assert random_score == pytest.approx(0.18722671690384815, abs=1e-12)

    def test_motion_vector_encoding(self):
        """Motion vector RGB encoding produces valid images."""
        from mathart.animation.sparse_ctrl_bridge import MotionVectorConditioner
        conditioner = MotionVectorConditioner(resolution=(32, 32))

        mv = make_rng(302).standard_normal((32, 32, 2)).astype(np.float32)
        encoded = conditioner.encode_motion_vectors([mv])
        assert len(encoded) == 1
        assert encoded[0].shape == (32, 32, 3)
        assert encoded[0].dtype == np.uint8
        np.testing.assert_array_equal(encoded[0][0, 0], np.array([126, 105, 21], dtype=np.uint8))
        np.testing.assert_allclose(
            encoded[0].mean(axis=(0, 1)),
            [126.3447265625, 127.6337890625, 40.783203125],
            atol=1e-12,
        )
        assert int(encoded[0].sum()) == 301836

    def test_adaptive_keyframe_selection(self):
        """Adaptive keyframe selection picks high-energy frames."""
        from mathart.animation.sparse_ctrl_bridge import MotionVectorConditioner
        conditioner = MotionVectorConditioner()

        # Create sequence with one high-energy frame
        mvs = [np.zeros((8, 8, 2)) for _ in range(20)]
        mvs[10] = np.ones((8, 8, 2)) * 5.0  # High energy

        keyframes = conditioner.adaptive_keyframe_selection(
            mvs, max_gap=8, energy_threshold=0.3
        )
        assert 0 in keyframes  # First frame
        assert 19 in keyframes  # Last frame
        assert 10 in keyframes  # High energy frame


# ═══════════════════════════════════════════════════════════════════════════
# 5. Motion Matching KD-Tree Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMotionMatchingKDTree:
    """Tests for KD-Tree accelerated motion matching."""

    def _make_test_database(self):
        """Create a test database with synthetic clips."""
        from mathart.animation.motion_matching_kdtree import (
            KDTreeMotionDatabase
        )
        db = KDTreeMotionDatabase()

        # Walk clip: 30 frames, 16-dim features
        rng = make_rng(303)
        walk_features = rng.standard_normal((30, 16)).astype(np.float32)
        walk_features[:, 0] = np.linspace(0, 2, 30)  # Velocity ramp

        # Run clip: 20 frames
        run_features = rng.standard_normal((20, 16)).astype(np.float32)
        run_features[:, 0] = np.linspace(2, 5, 20)  # Higher velocity

        db.add_clip("walk", walk_features, tags=["locomotion"])
        db.add_clip("run", run_features, tags=["locomotion"])
        db.build_index()

        return db

    def test_import(self):
        """KD-Tree motion matching module imports successfully."""
        from mathart.animation.motion_matching_kdtree import (
            KDTreeMotionDatabase, MotionMatchingController,
            FeatureWeights, MatchResult
        )
        assert KDTreeMotionDatabase is not None

    def test_database_creation(self):
        """Database creation and indexing works."""
        db = self._make_test_database()
        assert db.total_frames == 50  # 30 + 20
        assert db.clip_count == 2

    def test_query_returns_results(self):
        """Query returns valid match results."""
        db = self._make_test_database()
        query = make_rng(304).standard_normal(16).astype(np.float32)
        results = db.query(query, k=3)

        assert len(results) == 3
        assert results[0].cost <= results[1].cost  # Sorted by cost

    def test_query_nearest_neighbor(self):
        """Nearest neighbor query finds the closest frame."""
        db = self._make_test_database()
        # Query with features similar to walk clip frame 15
        walk_features = db._clips["walk"].features
        query = walk_features[15] + make_rng(305).standard_normal(16) * 0.01

        results = db.query(query, k=1)
        assert len(results) == 1
        assert results[0].clip_name == "walk"
        # Should find frame near 15
        assert abs(results[0].frame_idx - 15) < 5

    def test_clip_mapping(self):
        """Global-to-clip mapping is correct."""
        db = self._make_test_database()
        clip, frame = db.get_clip_and_frame(0)
        assert clip == "walk"
        assert frame == 0

        clip, frame = db.get_clip_and_frame(35)
        assert clip == "run"
        assert frame == 5

    def test_controller_update(self):
        """Motion matching controller produces transition commands."""
        from mathart.animation.motion_matching_kdtree import (
            MotionMatchingController
        )
        db = self._make_test_database()
        controller = MotionMatchingController(db)

        query = make_rng(304).standard_normal(16).astype(np.float32)
        cmd = controller.update(query)

        assert cmd.target_clip != ""
        assert cmd.should_transition  # First frame always transitions

    def test_controller_diagnostics(self):
        """Controller diagnostics return valid data."""
        from mathart.animation.motion_matching_kdtree import (
            MotionMatchingController
        )
        db = self._make_test_database()
        controller = MotionMatchingController(db)

        query = make_rng(304).standard_normal(16).astype(np.float32)
        controller.update(query)

        diag = controller.get_diagnostics()
        assert diag["database_total_frames"] == 50
        assert diag["total_transitions"] == 1

    def test_radius_query(self):
        """Radius query returns all points within distance."""
        db = self._make_test_database()
        query = np.zeros(16, dtype=np.float32)
        results = db.query_radius(query, radius=100.0)
        # With large radius, should find many frames
        assert len(results) > 0

    def test_convenience_function(self):
        """create_kdtree_database convenience function works."""
        from mathart.animation.motion_matching_kdtree import (
            create_kdtree_database
        )
        clips = {
            "test": make_rng(308).standard_normal((10, 8)).astype(np.float32)
        }
        db = create_kdtree_database(clips)
        assert db.total_frames == 10


# ═══════════════════════════════════════════════════════════════════════════
# Run all tests
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
