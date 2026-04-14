"""Tests for export bridge module."""
import json
import pytest
from pathlib import Path
from PIL import Image
from mathart.export.bridge import AssetExporter, ExportConfig, STANDARD_PPU


class TestExporter:
    @pytest.mark.unit
    def test_export_sprite(self, tmp_path):
        config = ExportConfig(output_dir=str(tmp_path), version=1)
        exporter = AssetExporter(config)
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        path = exporter.export_sprite(img, "test_spike", "Hazards", "SpikeTrap")
        assert path.exists()
        assert path.suffix == ".png"
        # Check metadata
        meta_path = path.with_suffix(".meta.json")
        assert meta_path.exists()
        with open(meta_path) as f:
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
        with open(meta_path) as f:
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
        with open(meta_path) as f:
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
        with open(meta_path) as f:
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
        with open(manifest_path) as f:
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
