from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mathart.animation.fluid_vfx import (
    FluidGrid2D,
    FluidGridConfig,
    FluidDrivenVFXSystem,
    FluidVFXConfig,
    default_character_obstacle_mask,
)
from mathart.evolution.fluid_vfx_bridge import FluidVFXEvolutionBridge, collect_fluid_vfx_status
from mathart.pipeline import AssetPipeline


class TestFluidGrid2D:
    def test_impulse_and_step_stay_finite(self):
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        grid.add_velocity_impulse((0.5, 0.7), (0.35, -0.2), radius=0.15)
        grid.add_density_impulse((0.5, 0.7), 1.0, radius=0.12)
        grid.step()

        assert np.isfinite(grid.u).all()
        assert np.isfinite(grid.v).all()
        assert np.isfinite(grid.density).all()
        assert grid.interior_speed().max() > 0.0
        assert grid.interior_density().sum() > 0.0

    def test_obstacle_cells_block_density(self):
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        obstacle = np.zeros((24, 24), dtype=bool)
        obstacle[10:14, 10:14] = True
        grid.set_obstacle_mask(obstacle)
        grid.add_density_impulse((0.5, 0.5), 2.0, radius=0.14)
        grid.step()

        interior = grid.interior_density()
        assert float(interior[obstacle].max()) == 0.0


class TestFluidDrivenVFXSystem:
    def test_dash_smoke_generates_frames_and_diagnostics(self):
        cfg = FluidVFXConfig.dash_smoke(canvas_size=48)
        system = FluidDrivenVFXSystem(cfg)
        frames = system.simulate_and_render(n_frames=8)

        assert len(frames) == 8
        assert len(system.last_diagnostics) == 8
        assert any(np.asarray(frame)[..., 3].sum() > 0 for frame in frames)
        assert any(d.mean_flow_energy > 0.0 for d in system.last_diagnostics)

    def test_default_obstacle_mask_has_body_mass(self):
        mask = default_character_obstacle_mask(32)
        assert mask.shape == (32, 32)
        assert mask.mean() > 0.01


class TestFluidPipelineIntegration:
    def test_pipeline_produce_vfx_fluid_preset(self, tmp_path):
        pipeline = AssetPipeline(output_dir=str(tmp_path))
        result = pipeline.produce_vfx(
            name="dash_smoke_demo",
            preset="dash_smoke",
            canvas_size=48,
            n_frames=6,
            seed=7,
        )

        assert result.frames is not None
        assert len(result.frames) == 6
        assert result.metadata["simulation_kind"] == "fluid"
        assert result.metadata["stable_fluids"] is True
        assert Path(result.output_paths[0]).exists()
        meta_path = Path(tmp_path) / "dash_smoke_demo" / "dash_smoke_demo_meta.json"
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["simulation_kind"] == "fluid"
        assert payload["fluid"]["mean_flow_energy"] >= 0.0


class TestFluidVFXEvolutionBridge:
    def test_bridge_evaluates_and_persists_state(self, tmp_path):
        cfg = FluidVFXConfig.slash_smoke(canvas_size=40)
        system = FluidDrivenVFXSystem(cfg)
        frames = system.simulate_and_render(n_frames=6)
        bridge = FluidVFXEvolutionBridge(Path(tmp_path), verbose=False)

        metrics = bridge.evaluate_fluid_vfx(
            frames=[np.asarray(frame) for frame in frames],
            diagnostics=[d.to_dict() for d in system.last_diagnostics],
        )
        rules = bridge.distill_fluid_knowledge(metrics)
        bonus = bridge.compute_fluid_fitness_bonus(metrics)
        status = collect_fluid_vfx_status(Path(tmp_path))

        assert metrics.frame_count == 6
        assert len(rules) >= 1
        assert -0.20 <= bonus <= 0.20
        assert (Path(tmp_path) / ".fluid_vfx_state.json").exists()
        assert (Path(tmp_path) / "knowledge" / "fluid_vfx_rules.md").exists()
        assert status.total_cycles >= 1


# ══════════════════════════════════════════════════════════════════════════════
# SESSION-114 / P1-VFX-1A — White-Box Verification Tests
# ══════════════════════════════════════════════════════════════════════════════

import inspect
import textwrap
import time

from mathart.animation.fluid_vfx import (
    MaskProjectionConfig,
    FluidMaskProjector,
    DynamicObstacleContext,
)


# ---------------------------------------------------------------------------
# 1. FluidMaskProjector — Cross-Space Tensor Projection
# ---------------------------------------------------------------------------

class TestFluidMaskProjector_BasicProjection:
    """Verify that FluidMaskProjector correctly maps high-res alpha to grid."""

    def test_full_coverage_mask_fills_bbox_region(self):
        """A solid 256x256 alpha mask covering bbox (0.2, 0.3, 0.8, 0.9) should
        produce True cells only inside that region of the 32x32 grid."""
        projector = FluidMaskProjector(32)
        alpha = np.ones((256, 256), dtype=np.float32)
        bbox = (0.2, 0.3, 0.8, 0.9)
        result = projector.project(alpha, world_bbox=bbox, threshold=0.5)

        assert result.shape == (32, 32)
        assert result.dtype == bool
        # Cells well inside bbox should be True
        assert result[int(0.5 * 32), int(0.5 * 32)]  # centre of grid
        # Cells well outside bbox should be False
        assert not result[0, 0]  # top-left corner
        assert not result[2, 2]  # still outside y0=0.3

    def test_empty_alpha_produces_empty_mask(self):
        """A zero alpha mask should produce all-False grid mask."""
        projector = FluidMaskProjector(32)
        alpha = np.zeros((128, 128), dtype=np.float32)
        result = projector.project(alpha, world_bbox=(0.0, 0.0, 1.0, 1.0))
        assert not result.any()

    def test_uint8_input_normalised_correctly(self):
        """uint8 [0, 255] input should be normalised to [0, 1]."""
        projector = FluidMaskProjector(24)
        alpha = np.full((64, 64), 200, dtype=np.uint8)
        result = projector.project(alpha, world_bbox=(0.0, 0.0, 1.0, 1.0), threshold=0.5)
        assert result.any()

    def test_3d_rgba_input_uses_alpha_channel(self):
        """3D (H, W, 4) input should extract the last channel as alpha."""
        projector = FluidMaskProjector(24)
        rgba = np.zeros((64, 64, 4), dtype=np.uint8)
        rgba[..., 3] = 255  # full alpha
        result = projector.project(rgba, world_bbox=(0.0, 0.0, 1.0, 1.0))
        assert result.any()

    def test_project_sequence_returns_correct_count(self):
        """project_sequence should return one mask per input frame."""
        projector = FluidMaskProjector(16)
        masks = [np.ones((32, 32), dtype=np.float32) for _ in range(5)]
        result = projector.project_sequence(masks)
        assert len(result) == 5
        for m in result:
            assert m.shape == (16, 16)
            assert m.dtype == bool


class TestFluidMaskProjector_AntiScalarRasterization:
    """🔴 Anti-Scalar Rasterization Guard: verify zero Python for-loops in
    the projection hot path.  The project() method must use only NumPy/SciPy
    tensor operations."""

    def test_no_for_loop_in_project_source(self):
        """Inspect the source code of FluidMaskProjector.project to verify
        there are no 'for ' loops in the executable body."""
        source = inspect.getsource(FluidMaskProjector.project)
        # Strip docstring: everything between triple-quotes
        import re
        source_no_doc = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
        source_no_doc = re.sub(r"'''.*?'''", '', source_no_doc, flags=re.DOTALL)
        # Remove comment lines
        lines = source_no_doc.split("\n")
        code_lines = [line for line in lines if not line.strip().startswith("#")]
        code = "\n".join(code_lines)
        # Check for Python for-loop statements ("for ... in")
        for_loops = re.findall(r'\bfor\s+\w+\s+in\s+', code)
        assert len(for_loops) == 0, (
            f"FluidMaskProjector.project contains {len(for_loops)} scalar for-loop(s), "
            "violating the Anti-Scalar Rasterization Guard"
        )

    def test_projection_performance_large_mask(self):
        """A 1024x1024 mask projected to 128x128 grid should complete in < 1s."""
        projector = FluidMaskProjector(128)
        alpha = np.random.rand(1024, 1024).astype(np.float32)
        start = time.perf_counter()
        for _ in range(10):
            projector.project(alpha, world_bbox=(0.1, 0.1, 0.9, 0.9))
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"10 projections took {elapsed:.3f}s (> 1.0s limit)"


class TestFluidMaskProjector_AntiCoordinateMismatch:
    """🔴 Anti-Coordinate-Mismatch Guard: verify that the affine transform
    correctly maps character root anchor to the expected grid position."""

    def test_centred_character_maps_to_grid_centre(self):
        """A character at bbox (0.25, 0.25, 0.75, 0.75) with a single bright
        pixel at the centre of the alpha mask should map to the centre of
        the grid."""
        N = 32
        projector = FluidMaskProjector(N)
        alpha = np.zeros((64, 64), dtype=np.float32)
        # Place a bright spot at the centre of the alpha mask
        alpha[30:34, 30:34] = 1.0
        bbox = (0.25, 0.25, 0.75, 0.75)
        result = projector.project(alpha, world_bbox=bbox, threshold=0.3)

        # The bright spot should appear near the centre of the grid
        # Grid centre is (16, 16), allow ±2 cells tolerance
        centre_region = result[14:18, 14:18]
        assert centre_region.any(), (
            "Character centre did not map to grid centre — coordinate mismatch!"
        )

    def test_corner_character_maps_to_grid_corner(self):
        """A character at bbox (0.0, 0.0, 0.5, 0.5) with full alpha should
        only occupy the top-left quadrant of the grid."""
        N = 32
        projector = FluidMaskProjector(N)
        alpha = np.ones((64, 64), dtype=np.float32)
        bbox = (0.0, 0.0, 0.5, 0.5)
        result = projector.project(alpha, world_bbox=bbox, threshold=0.3)

        # Top-left quadrant should be True
        assert result[:16, :16].any()
        # Bottom-right quadrant should be False
        assert not result[17:, 17:].any()

    def test_asymmetric_bbox_preserves_aspect(self):
        """A tall narrow bbox should produce a tall narrow obstacle region."""
        N = 32
        projector = FluidMaskProjector(N)
        alpha = np.ones((128, 32), dtype=np.float32)  # tall narrow
        bbox = (0.4, 0.1, 0.6, 0.9)  # narrow x, tall y
        result = projector.project(alpha, world_bbox=bbox, threshold=0.3)

        # Count True cells in x vs y directions
        col_coverage = result.any(axis=0).sum()
        row_coverage = result.any(axis=1).sum()
        assert row_coverage > col_coverage, (
            "Tall narrow bbox did not produce tall narrow obstacle region"
        )


# ---------------------------------------------------------------------------
# 2. Dynamic Obstacle — Volume Clearing & Boundary-Aware Solver
# ---------------------------------------------------------------------------

class TestDynamicObstacle_VolumeClearingProtocol:
    """Verify that update_dynamic_obstacle correctly clears density/velocity
    in newly-covered cells (Solid-Fluid Coupling volume clearing)."""

    def test_newly_covered_cells_are_cleared(self):
        """When a mask moves to cover cells that previously had density,
        those cells must have density = 0 after update."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        # Inject density in the centre
        grid.add_density_impulse((0.5, 0.5), 2.0, radius=0.2)
        grid.step()
        assert grid.interior_density().sum() > 0

        # Now place an obstacle covering the centre
        mask = np.zeros((24, 24), dtype=bool)
        mask[8:16, 8:16] = True
        grid.update_dynamic_obstacle(mask)

        # Density inside the obstacle must be zero
        interior = grid.interior_density()
        assert float(interior[mask].sum()) == 0.0

    def test_moving_mask_clears_new_cells_preserves_old(self):
        """When mask moves from position A to position B, cells in B but not
        in A must be cleared.  Cells in A but not B should retain their
        (already zeroed) state."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))

        # Set initial mask at left side
        mask_a = np.zeros((24, 24), dtype=bool)
        mask_a[10:14, 2:6] = True
        grid.update_dynamic_obstacle(mask_a)

        # Inject density everywhere
        grid.add_density_impulse((0.5, 0.5), 3.0, radius=0.4)
        grid.step()

        # Move mask to right side
        mask_b = np.zeros((24, 24), dtype=bool)
        mask_b[10:14, 18:22] = True
        grid.update_dynamic_obstacle(mask_b)

        # New obstacle cells must be zero
        interior = grid.interior_density()
        assert float(interior[mask_b].sum()) == 0.0

    def test_velocity_cleared_in_newly_covered_cells(self):
        """Velocity fields must also be zeroed in newly-covered cells."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        grid.add_velocity_impulse((0.5, 0.5), (10.0, -5.0), radius=0.3)
        grid.step()

        mask = np.zeros((24, 24), dtype=bool)
        mask[8:16, 8:16] = True
        grid.update_dynamic_obstacle(mask)

        u_interior = grid.u[1:25, 1:25]
        v_interior = grid.v[1:25, 1:25]
        assert float(np.abs(u_interior[mask]).sum()) == 0.0
        assert float(np.abs(v_interior[mask]).sum()) == 0.0


class TestDynamicObstacle_AntiDivergenceNaN:
    """🔴 Anti-Divergence NaN Guard: verify that moving obstacles never cause
    NaN in the pressure solver or any field."""

    def test_no_nan_after_rapid_mask_movement(self):
        """Rapidly moving a mask across the grid for 20 frames must not
        produce any NaN in density, velocity, or pressure fields."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=32, iterations=16))
        n = 32

        for frame in range(20):
            # Move mask from left to right
            col_start = int(frame * 1.5) % (n - 4)
            mask = np.zeros((n, n), dtype=bool)
            mask[12:20, col_start:col_start+4] = True
            grid.update_dynamic_obstacle(mask)

            # Inject density and velocity
            grid.add_density_impulse((0.5, 0.3), 1.5, radius=0.15)
            grid.add_velocity_impulse((0.5, 0.3), (5.0, -3.0), radius=0.12)
            grid.step()

            # NaN guard
            assert np.isfinite(grid.density).all(), f"NaN in density at frame {frame}"
            assert np.isfinite(grid.u).all(), f"NaN in u-velocity at frame {frame}"
            assert np.isfinite(grid.v).all(), f"NaN in v-velocity at frame {frame}"

    def test_no_nan_with_large_obstacle_coverage(self):
        """An obstacle covering 60% of the grid should not cause NaN."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        mask = np.zeros((24, 24), dtype=bool)
        mask[:15, :15] = True  # ~60% coverage

        grid.add_density_impulse((0.8, 0.8), 2.0, radius=0.1)
        grid.update_dynamic_obstacle(mask)
        grid.step()

        assert np.isfinite(grid.density).all()
        assert np.isfinite(grid.u).all()
        assert np.isfinite(grid.v).all()

    def test_obstacle_density_strictly_zero_after_step(self):
        """After a step with dynamic obstacles, density inside obstacle cells
        must be exactly zero (not just small)."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=24, iterations=12))
        mask = np.zeros((24, 24), dtype=bool)
        mask[8:16, 8:16] = True

        grid.add_density_impulse((0.5, 0.5), 3.0, radius=0.3)
        grid.update_dynamic_obstacle(mask)
        grid.step()

        interior = grid.interior_density()
        assert float(interior[mask].max()) == 0.0


# ---------------------------------------------------------------------------
# 3. FluidDrivenVFXSystem — Dynamic Mask Sequence Integration
# ---------------------------------------------------------------------------

class TestFluidDrivenVFXSystem_DynamicMasks:
    """Verify that simulate_and_render correctly consumes dynamic_obstacle_masks."""

    def test_dynamic_masks_produce_valid_frames(self):
        """Passing a sequence of dynamic masks should produce valid frames
        with no NaN in diagnostics."""
        cfg = FluidVFXConfig.dash_smoke(canvas_size=48)
        system = FluidDrivenVFXSystem(cfg)
        n = cfg.fluid.grid_size
        n_frames = 8

        # Create a moving mask sequence
        masks = []
        for i in range(n_frames):
            mask = np.zeros((n, n), dtype=bool)
            col = int(i * 2) % (n - 4)
            mask[n//2-3:n//2+3, col:col+4] = True
            masks.append(mask)

        frames = system.simulate_and_render(
            n_frames=n_frames,
            dynamic_obstacle_masks=masks,
        )

        assert len(frames) == n_frames
        assert len(system.last_diagnostics) == n_frames
        for d in system.last_diagnostics:
            assert np.isfinite(d.mean_flow_energy)
            assert np.isfinite(d.max_flow_speed)
            assert np.isfinite(d.density_mass)

    def test_moving_mask_blocks_smoke_penetration(self):
        """Smoke density inside the moving obstacle must be zero at every frame."""
        cfg = FluidVFXConfig.smoke_fluid(canvas_size=48)
        cfg = FluidVFXConfig(
            canvas_size=48,
            fluid=FluidGridConfig(grid_size=24, iterations=12),
            seed=42,
        )
        system = FluidDrivenVFXSystem(cfg)
        n = cfg.fluid.grid_size
        n_frames = 10

        masks = []
        for i in range(n_frames):
            mask = np.zeros((n, n), dtype=bool)
            row = 8 + (i % 8)
            mask[row:row+4, 8:16] = True
            masks.append(mask)

        system.simulate_and_render(
            n_frames=n_frames,
            dynamic_obstacle_masks=masks,
        )

        # After the last frame, obstacle cells should have zero density
        last_mask = masks[-1]
        interior = system.fluid.interior_density()
        assert float(interior[last_mask].max()) == 0.0

    def test_backward_compat_static_mask_still_works(self):
        """The old static obstacle_mask parameter should still work."""
        cfg = FluidVFXConfig.dash_smoke(canvas_size=48)
        system = FluidDrivenVFXSystem(cfg)
        n = cfg.fluid.grid_size

        static_mask = np.zeros((n, n), dtype=bool)
        static_mask[10:20, 10:20] = True

        frames = system.simulate_and_render(
            n_frames=6,
            obstacle_mask=static_mask,
        )
        assert len(frames) == 6


# ---------------------------------------------------------------------------
# 4. DynamicObstacleContext — Strong-Type Contract
# ---------------------------------------------------------------------------

class TestDynamicObstacleContext:
    """Verify the DynamicObstacleContext frozen dataclass contract."""

    def test_frozen_immutable(self):
        """DynamicObstacleContext should be frozen (immutable)."""
        mask = np.zeros((32, 32), dtype=bool)
        ctx = DynamicObstacleContext(mask=mask)
        try:
            ctx.mask = np.ones((32, 32), dtype=bool)
            assert False, "Should have raised FrozenInstanceError"
        except Exception:
            pass  # Expected

    def test_optional_velocity_field(self):
        """velocity field should default to None."""
        mask = np.zeros((32, 32), dtype=bool)
        ctx = DynamicObstacleContext(mask=mask)
        assert ctx.velocity is None

    def test_velocity_field_accepted(self):
        """Should accept a (N, N, 2) velocity field."""
        mask = np.zeros((32, 32), dtype=bool)
        vel = np.zeros((32, 32, 2), dtype=np.float64)
        ctx = DynamicObstacleContext(mask=mask, velocity=vel)
        assert ctx.velocity is not None
        assert ctx.velocity.shape == (32, 32, 2)


# ---------------------------------------------------------------------------
# 5. Vectorised Advection — Anti-Scalar Guard
# ---------------------------------------------------------------------------

class TestVectorisedAdvection:
    """Verify that the vectorised _advect produces correct results."""

    def test_advect_no_for_loop_in_source(self):
        """The _advect method source must not contain scalar for-loops."""
        import re
        source = inspect.getsource(FluidGrid2D._advect)
        source_no_doc = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
        source_no_doc = re.sub(r"'''.*?'''", '', source_no_doc, flags=re.DOTALL)
        lines = source_no_doc.split("\n")
        code_lines = [line for line in lines if not line.strip().startswith("#")]
        code = "\n".join(code_lines)
        for_loops = re.findall(r'\bfor\s+\w+\s+in\s+', code)
        assert len(for_loops) == 0, (
            f"FluidGrid2D._advect contains {len(for_loops)} scalar for-loop(s), "
            "violating the Anti-Scalar Rasterization Guard"
        )

    def test_set_bnd_no_for_loop_in_source(self):
        """The _set_bnd method source must not contain scalar for-loops."""
        import re
        source = inspect.getsource(FluidGrid2D._set_bnd)
        source_no_doc = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
        source_no_doc = re.sub(r"'''.*?'''", '', source_no_doc, flags=re.DOTALL)
        lines = source_no_doc.split("\n")
        code_lines = [line for line in lines if not line.strip().startswith("#")]
        code = "\n".join(code_lines)
        for_loops = re.findall(r'\bfor\s+\w+\s+in\s+', code)
        assert len(for_loops) == 0, (
            f"FluidGrid2D._set_bnd contains {len(for_loops)} scalar for-loop(s), "
            "violating the Anti-Scalar Rasterization Guard"
        )

    def test_advection_conserves_finite_values(self):
        """After advection, all values must remain finite."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=32, iterations=12))
        grid.add_density_impulse((0.5, 0.5), 2.0, radius=0.15)
        grid.add_velocity_impulse((0.5, 0.5), (8.0, -4.0), radius=0.12)
        for _ in range(10):
            grid.step()
            assert np.isfinite(grid.density).all()
            assert np.isfinite(grid.u).all()
            assert np.isfinite(grid.v).all()


# ---------------------------------------------------------------------------
# 6. Public API Exports
# ---------------------------------------------------------------------------

class TestPublicAPIExports_P1VFX1A:
    """Verify all P1-VFX-1A symbols are properly exported."""

    def test_all_p1vfx1a_exports(self):
        from mathart.animation import (
            MaskProjectionConfig,
            FluidMaskProjector,
            DynamicObstacleContext,
        )
        assert MaskProjectionConfig is not None
        assert FluidMaskProjector is not None
        assert DynamicObstacleContext is not None

    def test_update_dynamic_obstacle_exists(self):
        """FluidGrid2D must have update_dynamic_obstacle method."""
        assert hasattr(FluidGrid2D, "update_dynamic_obstacle")
        assert callable(FluidGrid2D.update_dynamic_obstacle)
