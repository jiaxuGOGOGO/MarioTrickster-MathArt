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
