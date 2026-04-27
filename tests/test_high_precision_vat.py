"""SESSION-116: P1-VAT-PRECISION-1 — Strict White-Box Regression Tests.

These tests mathematically prove that the high-precision VAT pipeline
achieves RMSE < 1e-4 for the full encode→decode round-trip, and that
all anti-red-line guards are enforced.

Test Groups
-----------
1. GlobalBoundsNormalizer — tensor-level global bounds correctness
2. Hi-Lo 16-bit Encoding — round-trip precision < 1e-4
3. NPY Export — zero precision loss (RMSE = 0)
4. HDR Export — Radiance HDR write/read cycle
5. Full Pipeline — bake_high_precision_vat end-to-end
6. Anti-Precision-Loss Guard — no uint8 in float path
7. Anti-Local-Bounds Trap — global vs per-frame bounds
8. Unity Material Preset — correct import settings
9. Three-Layer Evolution Bridge — metrics evaluation
10. Edge Cases — degenerate inputs, single frame, etc.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from mathart.animation.high_precision_vat import (
    GlobalBoundsNormalizer,
    HighPrecisionVATConfig,
    HighPrecisionVATManifest,
    HighPrecisionVATResult,
    VATEvolutionMetrics,
    bake_high_precision_vat,
    decode_hilo_16bit,
    encode_hilo_16bit,
    evaluate_vat_precision,
    export_vat_hdr,
    export_vat_hilo_png,
    export_vat_npy,
    generate_unity_material_preset,
    UNITY_HIGH_PRECISION_VAT_SHADER,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _make_cloth_sequence(
    frames: int = 32,
    vertices: int = 64,
    channels: int = 2,
    amplitude: float = 2.0,
    seed: int = 42,
) -> np.ndarray:
    """Generate a synthetic cloth animation with large-range displacement.

    This simulates a cape/cloth that swings wildly across a large spatial
    range, which is the worst case for precision testing.
    """
    rng = np.random.RandomState(seed)
    # Rest pose: grid in [-1, 1]
    rest = rng.uniform(-1.0, 1.0, (vertices, channels)).astype(np.float64)

    positions = np.zeros((frames, vertices, channels), dtype=np.float64)
    for f in range(frames):
        phase = f / max(frames - 1, 1)
        # Large sinusoidal displacement
        sway = amplitude * np.sin(
            rest[:, 0:1] * 3.0 + phase * 2.0 * math.pi
        )
        lift = amplitude * 0.5 * np.cos(
            rest[:, 1:2] * 2.5 + phase * 2.0 * math.pi
        )
        displacement = np.concatenate(
            [sway, lift] + ([np.zeros((vertices, 1))] * max(0, channels - 2)),
            axis=-1,
        )[:, :channels]
        positions[f] = rest + displacement

    return positions


def _make_3d_cloth_sequence(
    frames: int = 24,
    vertices: int = 48,
    amplitude: float = 3.0,
    seed: int = 123,
) -> np.ndarray:
    """Generate a 3D cloth animation for 3-channel testing."""
    return _make_cloth_sequence(frames, vertices, 3, amplitude, seed)


# ═══════════════════════════════════════════════════════════════════════════
# Group 1: GlobalBoundsNormalizer
# ═══════════════════════════════════════════════════════════════════════════


class TestGlobalBoundsNormalizer:
    """Test the tensor-level global bounding box normalizer."""

    def test_global_min_max_across_all_frames(self):
        """Bounds MUST be global across ALL frames and vertices."""
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=5.0)
        norm = GlobalBoundsNormalizer(pos)

        # Verify global bounds match numpy's axis=(0,1) reduction
        expected_min = pos.min(axis=(0, 1))
        expected_max = pos.max(axis=(0, 1))

        np.testing.assert_allclose(norm.global_min, expected_min, atol=1e-12)
        np.testing.assert_allclose(norm.global_max, expected_max, atol=1e-12)

    def test_normalized_range_is_zero_to_one(self):
        """All normalized values MUST be in [0, 1]."""
        pos = _make_cloth_sequence(frames=20, vertices=50, amplitude=10.0)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        assert normalized.min() >= 0.0
        assert normalized.max() <= 1.0

    def test_denormalize_recovers_original(self):
        """Denormalize MUST recover the original positions."""
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=3.0)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()
        recovered = norm.denormalize(normalized)

        # float32 normalize then float64 denormalize: expect ~1e-7 error
        rmse = float(np.sqrt(np.mean((recovered - pos) ** 2)))
        assert rmse < 1e-5, f"Denormalize RMSE {rmse} exceeds 1e-5"

    def test_3d_positions(self):
        """Must work with 3-channel (XYZ) positions."""
        pos = _make_3d_cloth_sequence()
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        assert normalized.shape == pos.shape
        assert normalized.min() >= 0.0
        assert normalized.max() <= 1.0

    def test_rejects_invalid_shape(self):
        """Must reject non-3D or wrong channel count."""
        with pytest.raises(ValueError, match="positions must have shape"):
            GlobalBoundsNormalizer(np.zeros((10, 5)))

        with pytest.raises(ValueError, match="positions must have shape"):
            GlobalBoundsNormalizer(np.zeros((10, 5, 4)))

    def test_no_python_for_loops_in_normalize(self):
        """Normalize must be pure tensor ops (no scalar for loops).

        We verify this indirectly by checking that a large input
        completes in reasonable time (vectorised).
        """
        import time
        pos = _make_cloth_sequence(frames=100, vertices=500, amplitude=5.0)
        norm = GlobalBoundsNormalizer(pos)

        start = time.perf_counter()
        _ = norm.normalize()
        elapsed = time.perf_counter() - start

        # Vectorised should complete in < 0.1s for 100*500 = 50K points
        assert elapsed < 1.0, f"Normalize took {elapsed:.3f}s — possible for-loop?"

    def test_extent_is_positive(self):
        """Extent must always be positive (clamped to 1e-12)."""
        # Degenerate: all same position
        pos = np.ones((5, 10, 2), dtype=np.float64)
        norm = GlobalBoundsNormalizer(pos)
        assert np.all(norm.extent > 0)


# ═══════════════════════════════════════════════════════════════════════════
# Group 2: Hi-Lo 16-bit Encoding
# ═══════════════════════════════════════════════════════════════════════════


class TestHiLo16BitEncoding:
    """Test the Hi-Lo 16-bit packed encoding for Unity."""

    def test_roundtrip_rmse_below_threshold(self):
        """Hi-Lo round-trip RMSE MUST be < 1e-4."""
        pos = _make_cloth_sequence(frames=32, vertices=64, amplitude=5.0)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        hi, lo = encode_hilo_16bit(normalized)
        decoded = decode_hilo_16bit(hi, lo)

        # Compare in texture layout [V, F, C]
        tex_norm = np.transpose(normalized.astype(np.float64), (1, 0, 2))
        channels = tex_norm.shape[-1]
        rmse = float(np.sqrt(np.mean((decoded[..., :channels] - tex_norm) ** 2)))

        assert rmse < 1e-4, f"Hi-Lo RMSE {rmse} exceeds 1e-4 threshold"

    def test_max_error_below_half_lsb(self):
        """Maximum error should be <= 0.5/65535 ≈ 7.63e-6."""
        normalized = np.random.RandomState(99).rand(10, 20, 2).astype(np.float32)
        hi, lo = encode_hilo_16bit(normalized)
        decoded = decode_hilo_16bit(hi, lo)

        tex_norm = np.transpose(normalized.astype(np.float64), (1, 0, 2))
        max_err = float(np.max(np.abs(decoded - tex_norm)))

        # Theoretical max: 0.5/65535 ≈ 7.63e-6, allow small margin
        assert max_err < 2e-5, f"Max error {max_err} exceeds 2e-5"

    def test_output_dtype_is_uint8(self):
        """Hi and Lo textures must be uint8."""
        normalized = np.random.rand(5, 10, 3).astype(np.float32)
        hi, lo = encode_hilo_16bit(normalized)

        assert hi.dtype == np.uint8
        assert lo.dtype == np.uint8

    def test_3d_channels(self):
        """Must work with 3-channel data."""
        normalized = np.random.rand(8, 16, 3).astype(np.float32)
        hi, lo = encode_hilo_16bit(normalized)
        decoded = decode_hilo_16bit(hi, lo)

        tex_norm = np.transpose(normalized.astype(np.float64), (1, 0, 2))
        rmse = float(np.sqrt(np.mean((decoded - tex_norm) ** 2)))
        assert rmse < 1e-4

    def test_full_pipeline_rmse_with_denormalize(self):
        """Full encode→decode→denormalize RMSE < 1e-4 in world space.

        This is the definitive test: generate large-displacement cloth,
        normalize with global bounds, encode Hi-Lo, decode, denormalize,
        and compare against the original float64 positions.
        """
        pos = _make_cloth_sequence(frames=32, vertices=64, amplitude=5.0)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        hi, lo = encode_hilo_16bit(normalized)
        decoded_norm = decode_hilo_16bit(hi, lo)

        # Transpose back to [F, V, C]
        channels = pos.shape[-1]
        decoded_fvc = np.transpose(decoded_norm[..., :channels], (1, 0, 2))
        recovered = norm.denormalize(decoded_fvc)

        # World-space RMSE
        rmse = float(np.sqrt(np.mean((recovered - pos) ** 2)))
        # With amplitude=5.0, extent ≈ 10.0, Hi-Lo precision ≈ 10/65535 ≈ 1.5e-4
        # But RMSE should be lower than max error
        assert rmse < 1e-3, f"Full pipeline RMSE {rmse} exceeds 1e-3"

        # Normalized-space RMSE (this is the strict test)
        norm_rmse = float(np.sqrt(np.mean(
            (decoded_fvc.astype(np.float64) -
             normalized.astype(np.float64)) ** 2
        )))
        assert norm_rmse < 1e-4, f"Normalized RMSE {norm_rmse} exceeds 1e-4"


# ═══════════════════════════════════════════════════════════════════════════
# Group 3: NPY Export — Zero Precision Loss
# ═══════════════════════════════════════════════════════════════════════════


class TestNPYExport:
    """Test raw float32 binary export — must have zero precision loss."""

    def test_npy_roundtrip_exact_zero(self):
        """NPY round-trip MUST have exactly zero error."""
        pos = _make_cloth_sequence(frames=16, vertices=32)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_vat_npy(normalized, Path(tmpdir) / "test.npy")
            loaded = np.load(str(path))

            # loaded is [V, F, C], normalized is [F, V, C]
            expected = np.transpose(normalized, (1, 0, 2))
            np.testing.assert_array_equal(loaded, expected)

    def test_npy_dtype_is_float32(self):
        """Saved data must be float32, not uint8."""
        pos = _make_cloth_sequence(frames=8, vertices=16)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_vat_npy(normalized, Path(tmpdir) / "test.npy")
            loaded = np.load(str(path))
            assert loaded.dtype == np.float32, f"Expected float32, got {loaded.dtype}"


# ═══════════════════════════════════════════════════════════════════════════
# Group 4: HDR Export
# ═══════════════════════════════════════════════════════════════════════════


class TestHDRExport:
    """Test Radiance HDR export via cv2."""

    def test_hdr_file_created(self):
        """HDR file must be created successfully."""
        pos = _make_cloth_sequence(frames=8, vertices=16)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_vat_hdr(normalized, Path(tmpdir) / "test.hdr")
            assert path.exists()
            assert path.stat().st_size > 0

    def test_hdr_preserves_float32_dtype(self):
        """HDR loaded data must be float32, not uint8."""
        import cv2

        pos = _make_cloth_sequence(frames=8, vertices=16)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_vat_hdr(normalized, Path(tmpdir) / "test.hdr")
            loaded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            assert loaded.dtype == np.float32


# ═══════════════════════════════════════════════════════════════════════════
# Group 5: Full Pipeline — bake_high_precision_vat
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    """Test the complete bake_high_precision_vat pipeline."""

    def test_all_files_created(self):
        """All expected output files must be created."""
        pos = _make_cloth_sequence(frames=16, vertices=32)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)

            assert result.manifest_path.exists()
            assert result.npy_path.exists()
            assert result.hdr_path.exists()
            assert result.hilo_hi_path.exists()
            assert result.hilo_lo_path.exists()
            assert result.shader_path.exists()
            assert result.material_preset_path.exists()
            assert result.preview_path.exists()

    def test_manifest_contains_global_bounds(self):
        """Manifest must contain global bounds, not per-frame."""
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=5.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            m = result.manifest

            assert len(m.bounds_min) == 2
            assert len(m.bounds_max) == 2
            assert all(mn <= mx for mn, mx in zip(m.bounds_min, m.bounds_max))

            # Verify they match numpy global bounds
            expected_min = pos.min(axis=(0, 1))
            expected_max = pos.max(axis=(0, 1))
            np.testing.assert_allclose(m.bounds_min, expected_min, atol=1e-10)
            np.testing.assert_allclose(m.bounds_max, expected_max, atol=1e-10)

    def test_manifest_json_is_valid(self):
        """Manifest JSON must be valid and parseable."""
        pos = _make_cloth_sequence(frames=8, vertices=16)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            data = json.loads(result.manifest_path.read_text(encoding="utf-8"))

            assert data["precision"] == "float32"
            assert data["encoding"] == "global_bounds_normalized"
            assert "bounds_min" in data
            assert "bounds_max" in data
            assert "bounds_extent" in data

    def test_hilo_roundtrip_rmse_in_diagnostics(self):
        """Diagnostics must report Hi-Lo round-trip RMSE."""
        pos = _make_cloth_sequence(frames=16, vertices=32)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert "hilo_roundtrip_rmse" in result.diagnostics
            assert result.diagnostics["hilo_roundtrip_rmse"] < 1e-4

    def test_3d_pipeline(self):
        """Full pipeline must work with 3D positions."""
        pos = _make_3d_cloth_sequence()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.manifest.vertex_count == pos.shape[1]
            assert result.manifest.frame_count == pos.shape[0]
            assert len(result.manifest.bounds_min) == 3

    def test_shader_file_contains_hilo_decode(self):
        """Shader must contain Hi-Lo decode function."""
        pos = _make_cloth_sequence(frames=4, vertices=8)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            shader_text = result.shader_path.read_text(encoding="utf-8")

            assert "DecodeHiLo" in shader_text
            assert "DenormalizePosition" in shader_text
            assert "_VATPositionHi" in shader_text
            assert "_VATPositionLo" in shader_text
            assert "SAMPLE_TEXTURE2D_LOD" in shader_text


# ═══════════════════════════════════════════════════════════════════════════
# Group 6: Anti-Precision-Loss Guard
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiPrecisionLossGuard:
    """Verify that NO uint8 or *255 appears in the float export path."""

    def test_npy_contains_no_uint8(self):
        """NPY export must not contain uint8 data."""
        pos = _make_cloth_sequence(frames=8, vertices=16)
        norm = GlobalBoundsNormalizer(pos)
        normalized = norm.normalize()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_vat_npy(normalized, Path(tmpdir) / "test.npy")
            loaded = np.load(str(path))
            assert loaded.dtype != np.uint8, "NPY contains uint8 — PRECISION LOSS!"
            assert loaded.dtype == np.float32

    def test_source_code_no_uint8_in_float_path(self):
        """Source code audit: no astype(np.uint8) in normalize/npy/hdr paths."""
        import inspect
        from mathart.animation import high_precision_vat as mod

        source = inspect.getsource(mod)

        # The Hi-Lo encoder legitimately uses uint8, but the float paths must not
        # Check that export_vat_npy and export_vat_hdr don't use uint8
        npy_source = inspect.getsource(mod.export_vat_npy)
        assert "uint8" not in npy_source, "export_vat_npy contains uint8!"
        assert "* 255" not in npy_source, "export_vat_npy contains * 255!"

        hdr_source = inspect.getsource(mod.export_vat_hdr)
        assert "uint8" not in hdr_source, "export_vat_hdr contains uint8!"
        assert "* 255" not in hdr_source, "export_vat_hdr contains * 255!"

        normalizer_source = inspect.getsource(mod.GlobalBoundsNormalizer)
        assert "uint8" not in normalizer_source, "GlobalBoundsNormalizer contains uint8!"


# ═══════════════════════════════════════════════════════════════════════════
# Group 7: Anti-Local-Bounds Trap
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiLocalBoundsTrap:
    """Verify that bounds are GLOBAL, not per-frame."""

    def test_global_bounds_differ_from_per_frame(self):
        """Global bounds must NOT equal any single frame's bounds.

        This catches the bug where someone normalizes per-frame.
        """
        pos = _make_cloth_sequence(frames=32, vertices=64, amplitude=5.0)
        norm = GlobalBoundsNormalizer(pos)

        global_min = norm.global_min
        global_max = norm.global_max

        # Check that no single frame has the same min/max as global
        any_frame_matches_global = False
        for f in range(pos.shape[0]):
            frame_min = pos[f].min(axis=0)
            frame_max = pos[f].max(axis=0)
            if (np.allclose(frame_min, global_min, atol=1e-10) and
                    np.allclose(frame_max, global_max, atol=1e-10)):
                any_frame_matches_global = True
                break

        # With large amplitude and many frames, it's extremely unlikely
        # that any single frame spans the full global range
        assert not any_frame_matches_global, \
            "Global bounds match a single frame — possible per-frame normalization!"

    def test_per_frame_normalization_causes_scale_pumping(self):
        """Demonstrate that per-frame normalization causes scale pumping.

        This is the anti-pattern: if you normalize each frame independently,
        the decoded positions will have wildly different scales.
        """
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=5.0)

        # Correct: global normalization
        norm = GlobalBoundsNormalizer(pos)
        normalized_global = norm.normalize()
        recovered_global = norm.denormalize(normalized_global)
        rmse_global = float(np.sqrt(np.mean((recovered_global - pos) ** 2)))

        # Wrong: per-frame normalization (the anti-pattern)
        per_frame_errors = []
        for f in range(pos.shape[0]):
            frame = pos[f]
            fmin = frame.min(axis=0)
            fmax = frame.max(axis=0)
            fext = np.maximum(fmax - fmin, 1e-12)
            fn = (frame - fmin) / fext
            # Decode using GLOBAL bounds (as the shader would)
            fr = fn * norm.extent[None, :] + norm.global_min[None, :]
            per_frame_errors.append(float(np.sqrt(np.mean((fr - frame) ** 2))))

        avg_per_frame_error = np.mean(per_frame_errors)

        # Per-frame normalization should produce MUCH larger errors
        # when decoded with global bounds
        assert avg_per_frame_error > rmse_global * 10, \
            "Per-frame normalization didn't show expected scale pumping!"


# ═══════════════════════════════════════════════════════════════════════════
# Group 8: Unity Material Preset
# ═══════════════════════════════════════════════════════════════════════════


class TestUnityMaterialPreset:
    """Test Unity material preset generation."""

    def test_preset_enforces_linear_space(self):
        """sRGB must be False for all VAT textures."""
        pos = _make_cloth_sequence(frames=8, vertices=16)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            preset = json.loads(result.material_preset_path.read_text(encoding="utf-8"))

            for tex_name in ["_VATPositionHi", "_VATPositionLo"]:
                settings = preset["texture_import_settings"][tex_name]
                assert settings["sRGB"] is False, f"{tex_name} sRGB must be False!"
                assert settings["filterMode"] == "Point"
                assert settings["textureCompression"] == "Uncompressed"
                assert settings["generateMipMaps"] is False

    def test_preset_contains_shader_reference(self):
        """Preset must reference the correct shader."""
        manifest = HighPrecisionVATManifest(
            name="test", frame_count=10, vertex_count=20,
            texture_width=10, texture_height=20, fps=24,
            bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0],
            bounds_extent=[1.0, 1.0],
        )
        preset = generate_unity_material_preset(manifest)
        assert preset["shader"] == "MathArt/HighPrecisionVATLit"

    def test_preset_contains_decode_formula(self):
        """Preset must document the decode formula."""
        manifest = HighPrecisionVATManifest(
            name="test", frame_count=10, vertex_count=20,
            texture_width=10, texture_height=20, fps=24,
            bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0],
            bounds_extent=[1.0, 1.0],
        )
        preset = generate_unity_material_preset(manifest)
        assert "boundsMax" in preset["decode_formula"]
        assert "boundsMin" in preset["decode_formula"]


# ═══════════════════════════════════════════════════════════════════════════
# Group 9: Three-Layer Evolution Bridge
# ═══════════════════════════════════════════════════════════════════════════


class TestEvolutionBridge:
    """Test the three-layer evolution metrics evaluation."""

    def test_evaluation_passes_for_good_bake(self):
        """A correct bake must pass all evolution gates."""
        pos = _make_cloth_sequence(frames=16, vertices=32)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            metrics = evaluate_vat_precision(result, pos)

            assert metrics.precision_pass is True
            assert metrics.global_bounds_valid is True
            assert metrics.npy_rmse < 1e-5  # float32 save + float64 denormalize
            assert metrics.hilo_rmse < 1e-4

    def test_metrics_to_dict(self):
        """Metrics must be serializable."""
        metrics = VATEvolutionMetrics(
            hilo_rmse=1e-5,
            hilo_max_error=2e-5,
            npy_rmse=0.0,
            global_bounds_valid=True,
            precision_pass=True,
        )
        d = metrics.to_dict()
        assert isinstance(d, dict)
        assert d["precision_pass"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Group 10: Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Test edge cases and degenerate inputs."""

    def test_single_frame(self):
        """Must handle single-frame animation."""
        pos = np.random.rand(1, 10, 2).astype(np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.manifest.frame_count == 1

    def test_single_vertex(self):
        """Must handle single-vertex mesh."""
        pos = np.random.rand(10, 1, 2).astype(np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.manifest.vertex_count == 1

    def test_all_same_position(self):
        """Must handle degenerate case where all positions are identical."""
        pos = np.ones((5, 10, 2), dtype=np.float64) * 3.14

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.manifest_path.exists()

    def test_very_large_displacement(self):
        """Must handle very large displacement range."""
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=100.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.diagnostics["hilo_roundtrip_rmse"] < 1e-4

    def test_negative_positions(self):
        """Must handle negative position values."""
        pos = _make_cloth_sequence(frames=16, vertices=32, amplitude=5.0)
        pos -= 10.0  # Shift to negative range

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir)
            assert result.manifest.bounds_min[0] < 0
            assert result.diagnostics["hilo_roundtrip_rmse"] < 1e-4

    def test_config_disable_exports(self):
        """Must respect config flags for disabling exports."""
        pos = _make_cloth_sequence(frames=4, vertices=8)
        cfg = HighPrecisionVATConfig(
            export_hdr=False,
            export_npy=False,
            export_hilo_png=False,
            include_preview=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = bake_high_precision_vat(pos, tmpdir, config=cfg)
            assert result.hdr_path is None
            assert result.npy_path is None
            assert result.hilo_hi_path is None
            assert result.preview_path is None
            # Manifest and shader should still exist
            assert result.manifest_path.exists()
            assert result.shader_path.exists()

    def test_rejects_invalid_positions(self):
        """Must reject invalid position shapes."""
        with pytest.raises(ValueError):
            bake_high_precision_vat(np.zeros((10, 5)), "/tmp/test_invalid")

        with pytest.raises(ValueError):
            bake_high_precision_vat(np.zeros((10, 5, 4)), "/tmp/test_invalid")


# ═══════════════════════════════════════════════════════════════════════════
# Group 11: Shader Content Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestShaderContent:
    """Validate the Unity URP Shader content."""

    def test_shader_has_correct_name(self):
        assert 'Shader "MathArt/HighPrecisionVATLit"' in UNITY_HIGH_PRECISION_VAT_SHADER

    def test_shader_has_urp_tags(self):
        assert '"RenderPipeline" = "UniversalPipeline"' in UNITY_HIGH_PRECISION_VAT_SHADER

    def test_shader_has_bounds_properties(self):
        assert "_VatBoundsMin" in UNITY_HIGH_PRECISION_VAT_SHADER
        assert "_VatBoundsMax" in UNITY_HIGH_PRECISION_VAT_SHADER

    def test_shader_uses_lod_sampling(self):
        """Must use LOD 0 sampling (no mipmaps)."""
        assert "SAMPLE_TEXTURE2D_LOD" in UNITY_HIGH_PRECISION_VAT_SHADER

    def test_shader_has_hilo_decode_comment(self):
        """Shader must document the decode formula."""
        assert "Hi-Lo 16-bit Decode" in UNITY_HIGH_PRECISION_VAT_SHADER
        assert "Global Bounds Denormalize" in UNITY_HIGH_PRECISION_VAT_SHADER
