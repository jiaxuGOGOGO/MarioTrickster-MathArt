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
    def test_produce_character_pack_rejects_unknown_preset(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        spec = CharacterSpec(name="bad_pack", preset="unknown")

        with pytest.raises(ValueError):
            pipeline.produce_character_pack(spec)

    @pytest.mark.unit
    def test_produce_character_pack_rejects_unknown_state(self, tmp_path: Path):
        pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False)
        spec = CharacterSpec(name="bad_state_pack", preset="mario", states=["idle", "attack"])

        with pytest.raises(ValueError):
            pipeline.produce_character_pack(spec)
