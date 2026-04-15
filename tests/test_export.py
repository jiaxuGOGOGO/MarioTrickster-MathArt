"""Tests for export bridge module."""
import json

import pytest
from PIL import Image

from mathart.export.bridge import AssetExporter, ExportConfig, STANDARD_PPU
from mathart.level.spec_bridge import AssetCategory as LevelAssetCategory
from mathart.level.spec_bridge import LevelSpec, LevelSpecBridge, LevelTheme


class TestExporter:
    @pytest.mark.unit
    def test_export_sprite(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path), version=1)
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        path = exporter.export_sprite(img, "test_spike", "Hazards", "SpikeTrap")
        assert path.exists()
        assert path.suffix == ".png"
        meta_path = path.with_suffix(".meta.json")
        assert meta_path.exists()
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["ppu"] == STANDARD_PPU
        assert meta["filter_mode"] == "Point"
        assert meta["element_key"] == "SpikeTrap"

    @pytest.mark.unit
    def test_export_spritesheet(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (256, 32), (0, 255, 0, 255))
        path = exporter.export_spritesheet(img, "fire", "Hazards", 8, "FireTrap")
        assert path.exists()
        meta_path = path.with_suffix(".meta.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["frame_count"] == 8
        assert meta["frame_width"] == 32
        assert meta["is_animated"] is True

    @pytest.mark.unit
    def test_character_pivot(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (0, 0, 255, 255))
        exporter.export_sprite(img, "mario", "Characters")
        meta_path = list(tmp_path.rglob("*.meta.json"))[0]
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["pivot"] == "bottom_center"
        assert meta["pivot_xy"] == [0.5, 0.0]

    @pytest.mark.unit
    def test_vfx_pivot(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (255, 255, 0, 255))
        exporter.export_sprite(img, "glow", "VFX")
        meta_path = list(tmp_path.rglob("*.meta.json"))[0]
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["pivot"] == "center"
        assert meta["pivot_xy"] == [0.5, 0.5]

    @pytest.mark.unit
    def test_manifest(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        exporter.export_sprite(img, "a", "Hazards")
        exporter.export_sprite(img, "b", "Environment")
        manifest_path = exporter.save_manifest()
        assert manifest_path.exists()
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["assets"]) == 2

    @pytest.mark.unit
    def test_rejects_non_rgba(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        img = Image.new("RGB", (32, 32), (255, 0, 0))
        with pytest.raises(ValueError, match="RGBA"):
            exporter.export_sprite(img, "bad", "Hazards")

    @pytest.mark.unit
    def test_naming_convention(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path), version=3)
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        path = exporter.export_sprite(img, "spike_trap", "Hazards", variant="b")
        assert "spike_trap_b_v03.png" in path.name

    @pytest.mark.unit
    def test_export_from_asset_spec_static_tile(self, tmp_path):
        level_bridge = LevelSpecBridge(project_root=tmp_path)
        level_spec = LevelSpec(
            level_id="level_1",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
        )
        asset_spec = level_bridge.to_asset_spec(level_spec, [LevelAssetCategory.TILE])
        exporter = AssetExporter(ExportConfig(output_dir=str(tmp_path), version=2))
        img = Image.new("RGBA", (16, 16), (120, 80, 40, 255))

        path = exporter.export_from_asset_spec(img, asset_spec, "level_1_tile")

        assert path.exists()
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        assert meta["category"] == "Environment"
        assert meta["level_id"] == "level_1"
        assert meta["source_sprite_name"] == "level_1_tile"
        assert meta["validation"]["valid"] is True
        assert meta["validation"]["expected"]["width"] == 16

    @pytest.mark.unit
    def test_export_from_asset_spec_rejects_wrong_size(self, tmp_path):
        level_bridge = LevelSpecBridge(project_root=tmp_path)
        level_spec = LevelSpec(
            level_id="level_1",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
        )
        asset_spec = level_bridge.to_asset_spec(level_spec, [LevelAssetCategory.TILE])
        exporter = AssetExporter(ExportConfig(output_dir=str(tmp_path)))
        img = Image.new("RGBA", (32, 16), (120, 80, 40, 255))

        with pytest.raises(ValueError, match="Width mismatch"):
            exporter.export_from_asset_spec(img, asset_spec, "level_1_tile")

    @pytest.mark.unit
    def test_export_sheet_from_asset_spec_uses_level_frame_count(self, tmp_path):
        level_bridge = LevelSpecBridge(project_root=tmp_path)
        asset_spec = level_bridge.to_asset_spec(
            level_bridge.create_mario_style_spec("world_1_1"),
            [LevelAssetCategory.PLAYER],
        )
        exporter = AssetExporter(ExportConfig(output_dir=str(tmp_path)))
        img = Image.new("RGBA", (128, 32), (255, 0, 0, 255))

        path = exporter.export_sheet_from_asset_spec(img, asset_spec, "mario")

        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        assert meta["frame_count"] == 8
        assert meta["frame_width"] == 16
        assert meta["category"] == "Characters"
        assert meta["render_mode"] == "flat_2d"
        assert meta["validation"]["actual"]["frame_count"] == 8

    @pytest.mark.unit
    def test_export_sheet_from_asset_spec_rejects_bad_sheet_geometry(self, tmp_path):
        level_bridge = LevelSpecBridge(project_root=tmp_path)
        asset_spec = level_bridge.to_asset_spec(
            level_bridge.create_mario_style_spec("world_1_1"),
            [LevelAssetCategory.PLAYER],
        )
        exporter = AssetExporter(ExportConfig(output_dir=str(tmp_path)))
        img = Image.new("RGBA", (64, 32), (255, 0, 0, 255))

        with pytest.raises(ValueError, match="Spritesheet width mismatch|Width mismatch"):
            exporter.export_sheet_from_asset_spec(img, asset_spec, "mario")


@pytest.mark.integration
class TestEndToEnd:
    """End-to-end integration tests."""

    def test_full_pipeline(self, tmp_path):
        """Generate palette → render SDF → export with metadata."""
        from mathart.oklab.palette import PaletteGenerator
        from mathart.sdf.effects import spike_sdf
        from mathart.sdf.renderer import render_sdf

        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=6, name="hazard")
        img = render_sdf(spike_sdf(), 32, 32, pal)

        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        path = exporter.export_sprite(img, "spike_trap", "Hazards", "SpikeTrap")

        assert path.exists()
        loaded = Image.open(path)
        assert loaded.mode == "RGBA"
        assert loaded.size == (32, 32)

    def test_animated_pipeline(self, tmp_path):
        """Generate palette → animate SDF → export sheet."""
        from mathart.oklab.palette import PaletteGenerator
        from mathart.sdf.effects import flame_sdf
        from mathart.sdf.renderer import render_spritesheet

        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=6)

        def anim(x, y, t):
            return flame_sdf(t=t)(x, y)

        sheet = render_spritesheet(anim, 8, 32, 32, pal)

        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        path = exporter.export_spritesheet(sheet, "fire", "Hazards", 8, "FireTrap")

        assert path.exists()
        loaded = Image.open(path)
        assert loaded.size == (256, 32)
