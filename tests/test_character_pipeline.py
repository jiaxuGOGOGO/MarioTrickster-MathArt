from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathart.pipeline import AssetPipeline, CharacterSpec


class TestCharacterPipeline:
    @pytest.mark.unit
    def test_produce_character_pack_exports_multistate_assets(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        spec = CharacterSpec(
            name="mario_pack",
            preset="mario",
            frame_width=32,
            frame_height=32,
            fps=12,
            frames_per_state=4,
            states=["idle", "run"],
        )

        result = pipeline.produce_character_pack(spec)

        assert result.image is not None
        assert result.spritesheet is not None
        assert result.spritesheet.size == (128, 64)
        assert len(result.frames) == 2
        assert set(result.metadata["states"].keys()) == {"idle", "run"}
        assert result.score >= 0.0

        output_files = {Path(p).name for p in result.output_paths}
        assert "mario_pack_character_atlas.png" in output_files
        assert "mario_pack_character_manifest.json" in output_files
        assert "mario_pack_palette.json" in output_files
        assert "mario_pack_idle_sheet.png" in output_files
        assert "mario_pack_run_sheet.png" in output_files

        manifest_path = tmp_path / "mario_pack" / "mario_pack_character_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["summary"]["state_count"] == 2
        assert manifest["atlas"]["size"] == {"w": 128, "h": 64}
        assert len(manifest["atlas"]["layout"]) == 2

    @pytest.mark.unit
    def test_produce_asset_pack_includes_character_results(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        results = pipeline.produce_asset_pack(
            pack_name="mini_pack",
            sprites=[],
            animations=[],
            characters=[
                CharacterSpec(
                    name="mini_mario",
                    preset="mario",
                    frames_per_state=3,
                    states=["idle", "run"],
                )
            ],
            include_textures=False,
        )

        names = {result.name for result in results}
        assert "mini_mario" in names
        summary_path = tmp_path / "mini_pack_summary.json"
        assert summary_path.exists()

    @pytest.mark.unit
    def test_produce_character_pack_with_evolution_exports_search_metadata(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False, seed=7)
        spec = CharacterSpec(
            name="evolved_mario",
            preset="mario",
            frame_width=32,
            frame_height=32,
            fps=12,
            frames_per_state=3,
            states=["idle", "run"],
            evolution_iterations=2,
            evolution_population=3,
            evolution_preview_states=["idle", "run"],
        )

        result = pipeline.produce_character_pack(spec)

        assert result.evolution_history
        assert len(result.evolution_history) == 3
        assert result.metadata["evolution"]["enabled"] is True
        assert result.metadata["summary"]["evolution_enabled"] is True
        assert result.metadata["evolution"]["best_score"] >= result.metadata["evolution"]["initial_score"]

        output_files = {Path(p).name for p in result.output_paths}
        assert "evolved_mario_character_evolution.json" in output_files

        evolution_path = tmp_path / "evolved_mario" / "evolved_mario_character_evolution.json"
        evolution = json.loads(evolution_path.read_text())
        assert evolution["iterations"] == 2
        assert evolution["population"] == 3
        assert evolution["elite_size"] == 3
        assert evolution["stagnation_patience"] == 2
        assert len(evolution["strength_history"]) == 3
        assert len(evolution["candidates"]) == 1 + 2 * 3
        assert "palette_hex" in evolution["best_character"]
        assert "silhouette_score" in evolution["best_character"]
        assert "state_distinction_score" in evolution["best_character"]
        assert "objective_weights" in evolution

    @pytest.mark.unit
    def test_character_evolution_reports_stagnation_recovery_metadata(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False, seed=11)
        spec = CharacterSpec(
            name="stagnation_mario",
            preset="mario",
            frame_width=32,
            frame_height=32,
            fps=12,
            frames_per_state=2,
            states=["idle", "run"],
            evolution_iterations=3,
            evolution_population=2,
            evolution_variation_strength=0.0,
            evolution_stagnation_patience=1,
            evolution_preview_states=["idle", "run"],
        )

        result = pipeline.produce_character_pack(spec)
        evolution = result.metadata["evolution"]

        assert evolution["elite_size"] >= 1
        assert evolution["stagnation_patience"] == 1
        assert evolution["stagnation_events"] >= 1
        assert len(evolution["strength_history"]) == 4
        assert any(candidate["parent_source"] == "restart" for candidate in evolution["candidates"][1:])

    @pytest.mark.unit
    def test_produce_character_pack_rejects_unknown_preset(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        spec = CharacterSpec(name="bad_pack", preset="unknown")

        with pytest.raises(ValueError):
            pipeline.produce_character_pack(spec)

    @pytest.mark.unit
    def test_build_umr_clip_for_state_uses_motion_lane_registry(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        clip = pipeline._build_umr_clip_for_state("run", frame_count=4, fps=12)

        assert clip.metadata["generator"] == "motion_lane_registry"
        assert clip.metadata["motion_lane"] == "run"
        assert all(frame.metadata["generator"] == "motion_lane_registry" for frame in clip.frames)
        assert all(frame.metadata["pipeline_source"] == "pipeline.run" for frame in clip.frames)

    @pytest.mark.unit
    def test_produce_character_pack_rejects_unknown_state(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        spec = CharacterSpec(name="bad_state_pack", preset="mario", states=["idle", "attack"])

        # SESSION-040: Unknown states now raise PipelineContractError (not ValueError)
        from mathart.pipeline_contract import PipelineContractError
        with pytest.raises(PipelineContractError):
            pipeline.produce_character_pack(spec)
