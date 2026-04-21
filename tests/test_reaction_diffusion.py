"""SESSION-119 White-Box Validation — Tensorized Gray-Scott Reaction-Diffusion.

These tests are the red-line enforcement suite for P1-NEW-2.  They exist to
fail loudly the moment any future refactor regresses the four architectural
guards stated in the task brief:

1.  **Anti-scalar-loop**: static AST inspection asserts that neither the
    solver step nor the PBR derivation contains a Python ``for``/``while``
    scanning individual pixels.
2.  **Anti-divergence / CFL**: after 1000+ Gray-Scott steps the U and V
    fields must stay finite and within [0, 1].
3.  **Normal-map unit-length**: every pixel of the derived normal map must
    have ``‖n‖ = 1`` (within float64 numerical noise).
4.  **Anti-boundary-artifact**: the Laplacian and the produced texture must
    be seamless under a half-grid wrap.

All tests use deterministic seeds and small grids (≤128²) so they run in
well under a minute.
"""
from __future__ import annotations

import ast
import inspect
import tempfile
import textwrap
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import convolve

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendRegistry, get_registry
from mathart.core.backend_types import BackendType, backend_type_value
from mathart.texture import reaction_diffusion as rd
from mathart.texture.reaction_diffusion import (
    GRAY_SCOTT_LAPLACIAN_KERNEL,
    GRAY_SCOTT_PRESETS,
    SOBEL_X,
    SOBEL_Y,
    GrayScottSolver,
    GrayScottSolverConfig,
    derive_pbr_from_concentration,
    encode_albedo_map,
    encode_height_map,
    encode_mask_map,
    encode_normal_map,
    get_preset,
    list_preset_names,
)


# ---------------------------------------------------------------------------
#  (1) Anti-scalar-loop static guard
# ---------------------------------------------------------------------------

class TestAntiScalarLoopGuard:
    """Static AST assertions that the hot path uses only vectorized ops."""

    def _collect_loops(self, source: str) -> list[ast.AST]:
        tree = ast.parse(textwrap.dedent(source))
        return [node for node in ast.walk(tree)
                if isinstance(node, (ast.For, ast.While))]

    def test_solver_step_has_no_python_loops(self) -> None:
        source = inspect.getsource(GrayScottSolver.step)
        loops = self._collect_loops(source)
        assert not loops, (
            f"GrayScottSolver.step contains {len(loops)} Python loop(s); "
            f"the hot path MUST be vectorized via scipy.ndimage.convolve."
        )

    def test_pbr_derivation_has_no_python_loops(self) -> None:
        source = inspect.getsource(derive_pbr_from_concentration)
        loops = self._collect_loops(source)
        assert not loops, (
            f"derive_pbr_from_concentration contains {len(loops)} Python "
            f"loop(s); it must be pure array math."
        )

    def test_solver_step_references_scipy_convolve(self) -> None:
        source = inspect.getsource(GrayScottSolver.step)
        assert "convolve(" in source, (
            "GrayScottSolver.step must invoke scipy.ndimage.convolve "
            "for the Laplacian."
        )

    def test_module_uses_wrap_mode_everywhere(self) -> None:
        source = inspect.getsource(rd)
        # Every convolve call in the module must use mode='wrap'.
        assert 'mode="wrap"' in source or "mode='wrap'" in source, (
            "Gray-Scott convolutions must use mode='wrap' for seamless "
            "periodic boundaries."
        )
        # And there must be at least as many wrap-mode uses as convolve uses.
        n_convolve = source.count("convolve(")
        n_wrap = source.count("mode=\"wrap\"") + source.count("mode='wrap'")
        assert n_wrap >= n_convolve, (
            f"Found {n_convolve} convolve() calls but only {n_wrap} "
            f"mode='wrap' invocations — one or more convolutions is "
            f"using a non-periodic boundary and will leak seams."
        )


# ---------------------------------------------------------------------------
#  (2) Kernel / stencil mathematical properties
# ---------------------------------------------------------------------------

class TestStencilMath:
    def test_laplacian_kernel_shape(self) -> None:
        assert GRAY_SCOTT_LAPLACIAN_KERNEL.shape == (3, 3)

    def test_laplacian_kernel_row_column_symmetry(self) -> None:
        k = GRAY_SCOTT_LAPLACIAN_KERNEL
        assert np.allclose(k, k.T), "Laplacian kernel must be transpose-symmetric"
        assert np.allclose(k, k[::-1, ::-1]), "Laplacian kernel must be central-symmetric"

    def test_laplacian_kernel_zero_sum(self) -> None:
        assert abs(float(GRAY_SCOTT_LAPLACIAN_KERNEL.sum())) < 1e-12, (
            "Any valid discrete Laplacian must sum to zero "
            "(constant fields have zero Laplacian)."
        )

    def test_laplacian_kernel_weights_match_karl_sims(self) -> None:
        k = GRAY_SCOTT_LAPLACIAN_KERNEL
        assert k[1, 1] == -1.0
        for idx in [(0, 1), (1, 0), (1, 2), (2, 1)]:
            assert k[idx] == 0.2, f"edge neighbour at {idx}"
        for idx in [(0, 0), (0, 2), (2, 0), (2, 2)]:
            assert k[idx] == 0.05, f"diagonal neighbour at {idx}"

    def test_constant_field_has_zero_laplacian(self) -> None:
        field = np.full((32, 32), 0.7)
        lap = convolve(field, GRAY_SCOTT_LAPLACIAN_KERNEL, mode="wrap")
        assert np.allclose(lap, 0.0, atol=1e-12)

    def test_sobel_kernels_match_normalized_definition(self) -> None:
        assert SOBEL_X.shape == (3, 3)
        assert SOBEL_Y.shape == (3, 3)
        # Sobel kernels should sum to zero (pure gradient, no DC).
        assert abs(float(SOBEL_X.sum())) < 1e-12
        assert abs(float(SOBEL_Y.sum())) < 1e-12
        # Constant field → zero gradient everywhere.
        flat = np.full((16, 16), 0.3)
        assert np.allclose(convolve(flat, SOBEL_X, mode="wrap"), 0.0)
        assert np.allclose(convolve(flat, SOBEL_Y, mode="wrap"), 0.0)


# ---------------------------------------------------------------------------
#  (3) Solver CFL / numerical stability
# ---------------------------------------------------------------------------

class TestNumericalStability:
    def test_dt_auto_clamp_to_cfl(self) -> None:
        cfg = GrayScottSolverConfig(dt=5.0, diffusion_u=1.0, diffusion_v=0.5)
        assert cfg.effective_dt() == pytest.approx(0.5)
        assert cfg.effective_dt() <= cfg.cfl_limit() + 1e-12

    def test_1000_steps_no_nan_no_inf(self) -> None:
        cfg = GrayScottSolverConfig(
            width=96, height=96, steps=1000, seed=20260421, preset=None  # noqa
        ) if False else GrayScottSolverConfig(
            width=96, height=96, steps=1000, seed=20260421,
        )
        solver = GrayScottSolver(cfg)
        state = solver.run()
        assert np.all(np.isfinite(state.u)), "U must not contain NaN/inf"
        assert np.all(np.isfinite(state.v)), "V must not contain NaN/inf"

    def test_1000_steps_bounds_in_unit_interval(self) -> None:
        cfg = GrayScottSolverConfig(
            width=96, height=96, steps=1000, seed=42,
        )
        solver = GrayScottSolver(cfg)
        state = solver.run()
        assert state.u.min() >= 0.0 - 1e-9
        assert state.u.max() <= 1.0 + 1e-9
        assert state.v.min() >= 0.0 - 1e-9
        assert state.v.max() <= 1.0 + 1e-9

    def test_solver_survives_hostile_dt(self) -> None:
        # Even a ludicrously large dt must be clamped and not blow up.
        cfg = GrayScottSolverConfig(
            width=64, height=64, steps=500, seed=7, dt=100.0,
        )
        solver = GrayScottSolver(cfg)
        state = solver.run()
        assert np.all(np.isfinite(state.u))
        assert np.all(np.isfinite(state.v))

    def test_step_is_deterministic(self) -> None:
        cfg = GrayScottSolverConfig(width=48, height=48, steps=200, seed=123)
        a = GrayScottSolver(cfg).run()
        b = GrayScottSolver(cfg).run()
        assert np.array_equal(a.u, b.u)
        assert np.array_equal(a.v, b.v)


# ---------------------------------------------------------------------------
#  (4) PBR derivation guarantees
# ---------------------------------------------------------------------------

class TestPBRDerivation:
    @pytest.fixture
    def evolved_state(self) -> rd.GrayScottState:
        cfg = GrayScottSolverConfig(
            width=96, height=96, steps=1500, seed=2024,
            feed=GRAY_SCOTT_PRESETS["CORAL"].feed,
            kill=GRAY_SCOTT_PRESETS["CORAL"].kill,
        )
        return GrayScottSolver(cfg).run()

    def test_normal_map_shape(self, evolved_state) -> None:
        pbr = derive_pbr_from_concentration(evolved_state.v)
        assert pbr.normal_rgb.shape == (96, 96, 3)
        assert pbr.normal_vec.shape == (96, 96, 3)
        assert pbr.albedo_rgb.shape == (96, 96, 3)
        assert pbr.height.shape == (96, 96)
        assert pbr.mask.shape == (96, 96)

    def test_normal_vectors_are_unit_length(self, evolved_state) -> None:
        pbr = derive_pbr_from_concentration(evolved_state.v)
        norms = np.linalg.norm(pbr.normal_vec, axis=-1)
        # Every pixel — strict 1.0 within float32 precision.
        assert norms.shape == (96, 96)
        assert np.max(np.abs(norms - 1.0)) < 1e-5, (
            f"Max deviation from unit length was {np.max(np.abs(norms - 1.0))}"
        )

    def test_normal_rgb_in_unit_interval(self, evolved_state) -> None:
        pbr = derive_pbr_from_concentration(evolved_state.v)
        assert pbr.normal_rgb.min() >= 0.0
        assert pbr.normal_rgb.max() <= 1.0

    def test_albedo_within_palette_gamut(self, evolved_state) -> None:
        preset = GRAY_SCOTT_PRESETS["CORAL"]
        pbr = derive_pbr_from_concentration(evolved_state.v, preset=preset)
        # Each channel must live within min/max of the two palette endpoints
        # (plus a tiny numerical fudge for float32 rounding).
        low = np.asarray(preset.palette_low)
        high = np.asarray(preset.palette_high)
        lo = np.minimum(low, high) - 1e-6
        hi = np.maximum(low, high) + 1e-6
        for c in range(3):
            assert pbr.albedo_rgb[..., c].min() >= lo[c]
            assert pbr.albedo_rgb[..., c].max() <= hi[c]

    def test_height_equals_clipped_v(self, evolved_state) -> None:
        pbr = derive_pbr_from_concentration(evolved_state.v)
        np.testing.assert_allclose(
            pbr.height.astype(np.float64),
            np.clip(evolved_state.v, 0.0, 1.0),
            atol=1e-6,
        )

    def test_flat_field_gives_pure_up_normal(self) -> None:
        flat = np.full((32, 32), 0.3, dtype=np.float64)
        pbr = derive_pbr_from_concentration(flat)
        # No gradient → every normal points straight up (0, 0, 1).
        assert np.allclose(pbr.normal_vec[..., 0], 0.0, atol=1e-9)
        assert np.allclose(pbr.normal_vec[..., 1], 0.0, atol=1e-9)
        assert np.allclose(pbr.normal_vec[..., 2], 1.0, atol=1e-9)


# ---------------------------------------------------------------------------
#  (5) Seamless periodic boundary
# ---------------------------------------------------------------------------

class TestSeamlessBoundary:
    def test_laplacian_is_periodic(self) -> None:
        # Build a deterministic field with non-trivial edges.
        rng = np.random.default_rng(0)
        field = rng.random((24, 24))
        lap = convolve(field, GRAY_SCOTT_LAPLACIAN_KERNEL, mode="wrap")
        # Shifting the field by (dy, dx) and re-convolving must yield the same
        # shifted result — the definition of periodicity.
        shifted = np.roll(field, shift=(5, -3), axis=(0, 1))
        lap_shift = convolve(shifted, GRAY_SCOTT_LAPLACIAN_KERNEL, mode="wrap")
        expected = np.roll(lap, shift=(5, -3), axis=(0, 1))
        np.testing.assert_allclose(lap_shift, expected, atol=1e-12)

    def test_evolved_texture_is_seamless(self) -> None:
        cfg = GrayScottSolverConfig(
            width=128, height=128, steps=1200, seed=99,
        )
        state = GrayScottSolver(cfg).run()
        v = state.v
        # A seamless texture has matching left/right and top/bottom columns
        # in the sense that the periodic wrap does not create a discontinuity
        # larger than the interior gradient magnitude.
        interior_grad = np.mean(np.abs(np.diff(v, axis=1)))
        seam_x = np.mean(np.abs(v[:, 0] - v[:, -1]))
        assert seam_x < 5.0 * (interior_grad + 1e-6), (
            f"seam_x={seam_x} vs interior_grad={interior_grad}"
        )
        interior_grad_y = np.mean(np.abs(np.diff(v, axis=0)))
        seam_y = np.mean(np.abs(v[0, :] - v[-1, :]))
        assert seam_y < 5.0 * (interior_grad_y + 1e-6), (
            f"seam_y={seam_y} vs interior_grad_y={interior_grad_y}"
        )

    def test_pbr_normal_wrap_continuity(self) -> None:
        cfg = GrayScottSolverConfig(
            width=64, height=64, steps=800, seed=11,
        )
        state = GrayScottSolver(cfg).run()
        pbr = derive_pbr_from_concentration(state.v)
        # The normal at column 0 and column W-1 should be close because the
        # derivative is computed with a wrap-mode Sobel kernel.
        diff = np.linalg.norm(
            pbr.normal_vec[:, 0, :] - pbr.normal_vec[:, -1, :], axis=-1
        )
        # Expect many columns to be close; require the median (not max) below
        # 0.5 since a few cells may sit on a real pattern edge.
        assert float(np.median(diff)) < 0.5


# ---------------------------------------------------------------------------
#  (6) Preset library
# ---------------------------------------------------------------------------

class TestPresetLibrary:
    def test_required_presets_present(self) -> None:
        names = list_preset_names()
        for required in ("CORAL", "MITOSIS", "MAZE", "SPOTS", "ALIEN_SKIN", "FLOW"):
            assert required in names, f"preset {required!r} missing"

    def test_preset_parameters_match_karl_sims(self) -> None:
        coral = get_preset("coral")
        assert coral.feed == pytest.approx(0.0545)
        assert coral.kill == pytest.approx(0.0620)
        mitosis = get_preset("MITOSIS")
        assert mitosis.feed == pytest.approx(0.0367)
        assert mitosis.kill == pytest.approx(0.0649)

    def test_preset_resolve_is_case_insensitive(self) -> None:
        assert get_preset("maze").name == "MAZE"
        assert get_preset("SpOtS").name == "SPOTS"

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(KeyError):
            get_preset("not_a_preset")


# ---------------------------------------------------------------------------
#  (7) Encoder contract
# ---------------------------------------------------------------------------

class TestEncoders:
    def test_encode_normal_dtype_and_range(self) -> None:
        arr = np.random.default_rng(0).random((16, 16, 3))
        out = encode_normal_map(arr)
        assert out.dtype == np.uint8
        assert out.min() >= 0 and out.max() <= 255

    def test_encode_height_dtype_and_range(self) -> None:
        arr = np.random.default_rng(1).random((16, 16))
        out = encode_height_map(arr)
        assert out.dtype == np.uint8
        assert out.shape == (16, 16)

    def test_encode_albedo_handles_out_of_range(self) -> None:
        arr = np.array([[[1.5, -0.2, 0.5]]], dtype=np.float32)
        out = encode_albedo_map(arr)
        assert out[0, 0, 0] == 255
        assert out[0, 0, 1] == 0
        assert 120 <= out[0, 0, 2] <= 140

    def test_encode_mask(self) -> None:
        arr = np.array([[0.0, 0.5, 1.0]])
        out = encode_mask_map(arr)
        assert out.dtype == np.uint8
        assert out[0, 0] == 0
        assert out[0, 2] == 255


# ---------------------------------------------------------------------------
#  (8) Backend registry + manifest contract
# ---------------------------------------------------------------------------

class TestBackendRegistration:
    def test_backend_type_enum_value(self) -> None:
        assert BackendType.REACTION_DIFFUSION.value == "reaction_diffusion"

    def test_aliases_resolve(self) -> None:
        for alias in ("gray_scott", "organic_texture", "turing_pattern",
                      "reaction_diffusion_texture"):
            assert backend_type_value(alias) == "reaction_diffusion"

    def test_backend_is_discoverable(self) -> None:
        reg = get_registry()
        meta, cls = reg.get_or_raise("reaction_diffusion")
        assert meta.name == "reaction_diffusion"
        assert ArtifactFamily.MATERIAL_BUNDLE.value in meta.artifact_families
        assert cls.__name__ == "ReactionDiffusionBackend"


class TestBackendExecution:
    @pytest.fixture
    def tmp_output_dir(self) -> Path:
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def _run(self, tmp_output_dir: Path, **overrides) -> ArtifactManifest:
        reg = get_registry()
        _, cls = reg.get_or_raise("reaction_diffusion")
        ctx: dict = {
            "preset": "SPOTS",
            "width": 64,
            "height": 64,
            "steps": 300,
            "output_dir": str(tmp_output_dir),
            "name": "rd_test",
        }
        ctx.update(overrides)
        return cls().execute(ctx)

    def test_manifest_family_and_backend_type(self, tmp_output_dir: Path) -> None:
        m = self._run(tmp_output_dir)
        assert m.artifact_family == ArtifactFamily.MATERIAL_BUNDLE.value
        assert m.backend_type == BackendType.REACTION_DIFFUSION.value

    def test_manifest_outputs_exist(self, tmp_output_dir: Path) -> None:
        m = self._run(tmp_output_dir)
        for role in ("albedo", "normal", "height", "mask",
                     "fields_npz", "bundle_manifest"):
            assert role in m.outputs, f"missing output role {role!r}"
            assert Path(m.outputs[role]).exists(), m.outputs[role]

    def test_manifest_texture_channels_payload(self, tmp_output_dir: Path) -> None:
        m = self._run(tmp_output_dir)
        payload = m.metadata.get("payload", {})
        channels = payload.get("texture_channels", {})
        for role in ("albedo", "normal", "height", "mask"):
            assert role in channels, f"channel {role!r} missing from payload"
            entry = channels[role]
            assert entry["dimensions"] == {"width": 64, "height": 64}
            assert entry["bit_depth"] == 8
            assert entry["engine_slot"]["unity"]
            assert entry["engine_slot"]["godot"]

    def test_manifest_quality_metrics(self, tmp_output_dir: Path) -> None:
        m = self._run(tmp_output_dir)
        qm = m.quality_metrics
        assert qm["channel_count"] >= 4.0
        # Normal map unit-length error must be essentially zero.
        assert qm["normal_unit_length_error"] < 1e-4

    def test_channel_subset(self, tmp_output_dir: Path) -> None:
        m = self._run(tmp_output_dir, channels=["normal"])
        # The backend always falls back to emit albedo to satisfy the schema.
        assert "normal" in m.outputs
        assert "albedo" in m.outputs

    def test_validate_config_clamps_small_grid(self, tmp_output_dir: Path) -> None:
        reg = get_registry()
        _, cls = reg.get_or_raise("reaction_diffusion")
        backend = cls()
        cfg, warnings = backend.validate_config({"width": 2, "height": 2})
        assert cfg["width"] >= 16
        assert cfg["height"] >= 16
        assert any("too small" in w for w in warnings)

    def test_advection_field_hook_accepted(self, tmp_output_dir: Path) -> None:
        reg = get_registry()
        _, cls = reg.get_or_raise("reaction_diffusion")
        backend = cls()
        adv = np.zeros((2, 64, 64), dtype=np.float64)
        cfg, _warnings = backend.validate_config({
            "width": 64, "height": 64, "advection_field": adv,
        })
        assert cfg["advection_field"] is not None
        assert cfg["advection_field"].shape == (2, 64, 64)

    def test_advection_scheme_stored(self, tmp_output_dir: Path) -> None:
        m = self._run(
            tmp_output_dir,
            advection_scheme="semi_lagrangian",
        )
        assert m.metadata["advection_scheme"] == "semi_lagrangian"
        # No advection field attached → attachment flag must be False.
        assert m.metadata["advection_attached"] is False
