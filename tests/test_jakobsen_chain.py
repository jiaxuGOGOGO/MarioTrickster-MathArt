import json
from pathlib import Path

import numpy as np

from mathart.animation.jakobsen_chain import (
    BodyCollisionCircle,
    JakobsenSecondaryChain,
    SecondaryChainConfig,
    SecondaryChainProjector,
    create_default_secondary_chain_configs,
)
from mathart.animation.unified_motion import MotionRootTransform, pose_to_umr
from mathart.pipeline import AssetPipeline, CharacterSpec
from mathart.evolution.jakobsen_bridge import JakobsenEvolutionBridge, collect_jakobsen_chain_status


class TestJakobsenSecondaryChain:
    def test_chain_tracks_anchor_and_preserves_lengths(self):
        config = SecondaryChainConfig(
            name="cape_test",
            anchor_joint="chest",
            segment_count=6,
            segment_length=0.08,
            iterations=6,
            gravity=(0.0, -0.4),
            ground_y=None,
        )
        chain = JakobsenSecondaryChain(config)

        anchors = [
            (0.0, 0.75),
            (0.02, 0.75),
            (0.08, 0.76),
            (0.16, 0.76),
        ]
        snapshot = None
        for anchor in anchors:
            snapshot = chain.step(anchor, dt=1.0 / 30.0, anchor_velocity=(2.0, 0.0))

        assert snapshot is not None
        assert len(snapshot.points) == 6
        assert np.allclose(snapshot.points[0], anchors[-1], atol=1e-6)
        assert snapshot.diagnostics.max_constraint_error < 0.025
        assert snapshot.diagnostics.tip_lag > 0.10

    def test_collision_circle_pushes_particles_outside_proxy(self):
        config = SecondaryChainConfig(
            name="hair_test",
            anchor_joint="head",
            segment_count=5,
            segment_length=0.06,
            iterations=5,
            gravity=(0.0, -0.2),
            particle_radius=0.01,
            ground_y=None,
        )
        chain = JakobsenSecondaryChain(config)
        circle = BodyCollisionCircle(center=(0.0, 0.36), radius=0.18, label="head")

        snapshot = None
        for _ in range(4):
            snapshot = chain.step((0.0, 0.36), dt=1.0 / 60.0, collision_circles=[circle])

        assert snapshot is not None
        for point in snapshot.points[1:]:
            dist = np.linalg.norm(np.array(point) - np.array(circle.center))
            assert dist >= circle.radius + config.particle_radius - 1e-6
        assert snapshot.diagnostics.collision_count >= 1


class TestSecondaryChainProjector:
    def test_projector_writes_metadata_into_umr_frame(self):
        projector = SecondaryChainProjector(
            configs=[cfg for cfg in create_default_secondary_chain_configs(3.0) if cfg.name == "cape"],
            head_units=3.0,
        )
        frame = pose_to_umr(
            pose={"chest": 0.05, "neck": 0.02, "head": -0.03},
            root_transform=MotionRootTransform(x=0.1, y=0.0, velocity_x=1.4, velocity_y=0.0),
            frame_index=0,
            source_state="run",
        )

        projected = projector.step_frame(frame, dt=1.0 / 24.0)

        assert projected.metadata["secondary_chain_projected"] is True
        assert projected.metadata["secondary_chain_count"] == 1
        assert "cape" in projected.metadata["secondary_chains"]
        assert projected.metadata["secondary_chain_debug"]["cape"]["anchor_joint"] == "chest"


class TestJakobsenPipelineIntegration:
    def test_character_pipeline_persists_secondary_chain_metadata(self, tmp_path):
        pipeline = AssetPipeline(output_dir=str(tmp_path))
        spec = CharacterSpec(
            name="jakobsen_character",
            preset="mario",
            states=["idle"],
            frames_per_state=4,
            fps=8,
            enable_secondary_chains=True,
            secondary_chain_presets=["cape"],
        )
        result = pipeline.produce_character_pack(spec)

        assert result.metadata["character"]["secondary_chain_config"]["enabled"] is True
        assert result.metadata["character"]["secondary_chain_config"]["presets"] == ["cape"]

        state_meta = result.metadata["states"]["idle"]
        umr_path = Path(tmp_path) / spec.name / state_meta["motion_bus"]["file"]
        payload = json.loads(umr_path.read_text(encoding="utf-8"))
        first_frame_meta = payload["frames"][0]["metadata"]

        assert first_frame_meta["secondary_chain_projected"] is True
        assert first_frame_meta["secondary_chain_count"] == 1
        assert "cape" in first_frame_meta["secondary_chains"]


class TestJakobsenEvolutionBridge:
    def test_bridge_evaluates_distills_and_persists_state(self, tmp_path):
        bridge = JakobsenEvolutionBridge(Path(tmp_path), verbose=False)
        diagnostics = [
            {
                "cape": {
                    "mean_constraint_error": 0.012,
                    "max_constraint_error": 0.020,
                    "tip_lag": 0.18,
                    "collision_count": 1,
                    "stretch_ratio": 1.08,
                    "anchor_speed": 1.2,
                },
                "hair": {
                    "mean_constraint_error": 0.010,
                    "max_constraint_error": 0.018,
                    "tip_lag": 0.11,
                    "collision_count": 0,
                    "stretch_ratio": 1.04,
                    "anchor_speed": 1.0,
                },
            }
        ]

        metrics = bridge.evaluate_secondary_chains(diagnostics)
        rules = bridge.distill_secondary_chain_knowledge(metrics)
        bonus = bridge.compute_secondary_chain_fitness_bonus(metrics)
        status = collect_jakobsen_chain_status(Path(tmp_path))

        assert metrics.pass_gate is True
        assert metrics.chain_count == 2
        assert len(rules) >= 1
        assert -0.20 <= bonus <= 0.20
        assert (Path(tmp_path) / "workspace" / "evolution_states" / "jakobsen_chain_state.json").exists()
        assert (Path(tmp_path) / "knowledge" / "jakobsen_secondary_chain_rules.md").exists()
        assert status.total_cycles >= 1
