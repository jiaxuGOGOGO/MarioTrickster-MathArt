"""Tests for noise texture generator (TASK-004) and workspace manager."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mathart.sdf.noise import (
    perlin_2d,
    simplex_2d,
    fbm,
    ridged_noise,
    turbulence,
    domain_warp,
    render_noise_texture,
    generate_texture,
    TEXTURE_PRESETS,
    COLORMAPS,
)
from mathart.workspace.manager import WorkspaceManager


# ── Perlin Noise Tests ────────────────────────────────────────────────────────

class TestPerlinNoise:
    def test_output_shape(self):
        result = perlin_2d(64, 32, scale=4.0, seed=0)
        assert result.shape == (32, 64)

    def test_value_range(self):
        result = perlin_2d(128, 128, scale=8.0, seed=42)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_deterministic(self):
        a = perlin_2d(32, 32, scale=4.0, seed=123)
        b = perlin_2d(32, 32, scale=4.0, seed=123)
        np.testing.assert_array_equal(a, b)

    def test_different_seeds(self):
        a = perlin_2d(32, 32, scale=4.0, seed=0)
        b = perlin_2d(32, 32, scale=4.0, seed=999)
        assert not np.array_equal(a, b)

    def test_scale_affects_frequency(self):
        low = perlin_2d(64, 64, scale=2.0, seed=0)
        high = perlin_2d(64, 64, scale=16.0, seed=0)
        # Higher scale should have more variation
        assert np.std(np.diff(high, axis=1)) > np.std(np.diff(low, axis=1)) * 0.5

    def test_offset(self):
        a = perlin_2d(32, 32, scale=4.0, offset_x=0.0, seed=0)
        b = perlin_2d(32, 32, scale=4.0, offset_x=100.0, seed=0)
        assert not np.array_equal(a, b)


# ── Simplex Noise Tests ──────────────────────────────────────────────────────

class TestSimplexNoise:
    def test_output_shape(self):
        result = simplex_2d(64, 32, scale=4.0, seed=0)
        assert result.shape == (32, 64)

    def test_value_range(self):
        result = simplex_2d(128, 128, scale=8.0, seed=42)
        assert result.min() >= -0.1  # Simplex can slightly exceed [0,1]
        assert result.max() <= 1.1

    def test_deterministic(self):
        a = simplex_2d(32, 32, scale=4.0, seed=123)
        b = simplex_2d(32, 32, scale=4.0, seed=123)
        np.testing.assert_array_equal(a, b)


# ── fBm Tests ────────────────────────────────────────────────────────────────

class TestFBM:
    def test_output_shape(self):
        result = fbm(64, 64, octaves=4, seed=0)
        assert result.shape == (64, 64)

    def test_value_range(self):
        result = fbm(128, 128, octaves=6, seed=42)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_more_octaves_more_detail(self):
        low = fbm(64, 64, octaves=1, seed=0)
        high = fbm(64, 64, octaves=6, seed=0)
        # More octaves should add high-frequency detail
        assert np.std(np.diff(high, axis=1)) >= np.std(np.diff(low, axis=1)) * 0.8

    def test_perlin_vs_simplex_base(self):
        a = fbm(32, 32, noise_func="perlin", seed=0)
        b = fbm(32, 32, noise_func="simplex", seed=0)
        assert a.shape == b.shape
        assert not np.array_equal(a, b)


# ── Ridged Noise Tests ───────────────────────────────────────────────────────

class TestRidgedNoise:
    def test_output_shape(self):
        result = ridged_noise(64, 64, seed=0)
        assert result.shape == (64, 64)

    def test_value_range(self):
        result = ridged_noise(128, 128, seed=42)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


# ── Turbulence Tests ─────────────────────────────────────────────────────────

class TestTurbulence:
    def test_output_shape(self):
        result = turbulence(64, 64, seed=0)
        assert result.shape == (64, 64)

    def test_value_range(self):
        result = turbulence(128, 128, seed=42)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


# ── Domain Warp Tests ────────────────────────────────────────────────────────

class TestDomainWarp:
    def test_output_shape(self):
        result = domain_warp(64, 64, seed=0)
        assert result.shape == (64, 64)

    def test_value_range(self):
        result = domain_warp(64, 64, seed=42)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


# ── Rendering Tests ──────────────────────────────────────────────────────────

class TestRenderNoiseTexture:
    def test_default_gray(self):
        noise = perlin_2d(32, 32, seed=0)
        img = render_noise_texture(noise)
        assert img.mode == "RGBA"
        assert img.size == (32, 32)

    def test_all_colormaps(self):
        noise = perlin_2d(32, 32, seed=0)
        for name in COLORMAPS:
            img = render_noise_texture(noise, colormap=name)
            assert img.mode == "RGBA"
            assert img.size == (32, 32)

    def test_palette_quantization(self):
        noise = perlin_2d(32, 32, seed=0)
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        img = render_noise_texture(noise, palette=palette)
        arr = np.array(img)
        # All pixels should be one of the 3 palette colors
        unique_rgb = set()
        for row in arr:
            for px in row:
                unique_rgb.add(tuple(px[:3]))
        assert unique_rgb.issubset({(255, 0, 0), (0, 255, 0), (0, 0, 255)})

    def test_transparency_threshold(self):
        noise = np.full((16, 16), 0.3)
        img = render_noise_texture(noise, transparent_below=0.5)
        arr = np.array(img)
        assert np.all(arr[:, :, 3] == 0)  # All below threshold → transparent


# ── Preset / generate_texture Tests ──────────────────────────────────────────

class TestGenerateTexture:
    def test_all_presets(self):
        for name in TEXTURE_PRESETS:
            img = generate_texture(preset=name, width=32, height=32, seed=0)
            assert img.mode == "RGBA"
            assert img.size == (32, 32)

    def test_invalid_preset(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            generate_texture(preset="nonexistent")

    def test_custom_size(self):
        img = generate_texture(preset="terrain", width=128, height=64, seed=0)
        assert img.size == (128, 64)

    def test_colormap_override(self):
        img = generate_texture(preset="terrain", width=32, height=32, colormap="magic")
        assert img.mode == "RGBA"

    def test_deterministic(self):
        a = generate_texture(preset="lava", width=32, height=32, seed=42)
        b = generate_texture(preset="lava", width=32, height=32, seed=42)
        assert np.array_equal(np.array(a), np.array(b))


# ── Math Registry Integration ────────────────────────────────────────────────

class TestNoiseRegistryIntegration:
    def test_texture_capability_covered(self):
        from mathart.evolution.math_registry import MathModelRegistry, ModelCapability
        registry = MathModelRegistry()
        tex_models = registry.find_by_capability(ModelCapability.TEXTURE)
        assert len(tex_models) >= 1
        assert tex_models[0].name == "noise_texture_generator"
        assert tex_models[0].status == "stable"

    def test_registry_has_10_models(self):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        assert len(registry.list_all()) == 10  # Was 9, now 10 with noise


# ── Workspace Manager Tests ──────────────────────────────────────────────────

class TestWorkspaceManager:
    @pytest.fixture
    def ws(self, tmp_path):
        """Create a workspace manager with a temp project root."""
        # Create a minimal project structure
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "knowledge").mkdir()
        return WorkspaceManager(project_root=tmp_path)

    def test_init_workspace_creates_dirs(self, ws):
        created = ws.init_workspace()
        assert (ws.inbox / "sprites").is_dir()
        assert (ws.inbox / "sheets").is_dir()
        assert (ws.inbox / "knowledge").is_dir()
        assert (ws.inbox / "processed").is_dir()
        assert (ws.output / "textures").is_dir()
        assert (ws.output / "effects").is_dir()
        assert (ws.output / "palettes").is_dir()
        assert (ws.output / "characters").is_dir()
        assert (ws.output / "levels").is_dir()
        assert (ws.output / "exports").is_dir()
        assert len(created["inbox"]) >= 4
        assert len(created["output"]) >= 6

    def test_init_workspace_creates_readme(self, ws):
        ws.init_workspace()
        readme = ws.inbox / "README.md"
        assert readme.exists()
        content = readme.read_text(encoding="utf-8")
        assert "sprites" in content

    def test_init_workspace_idempotent(self, ws):
        ws.init_workspace()
        ws.init_workspace()  # Should not raise
        assert (ws.inbox / "sprites").is_dir()

    def test_scan_empty_inbox(self, ws):
        ws.init_workspace()
        found = ws.scan_inbox()
        assert found["sprites"] == []
        assert found["sheets"] == []
        assert found["knowledge"] == []

    def test_scan_finds_sprites(self, ws):
        ws.init_workspace()
        # Create a test image in sprites/
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        img.save(str(ws.inbox / "sprites" / "test.png"))
        found = ws.scan_inbox()
        assert len(found["sprites"]) == 1
        assert found["sprites"][0].name == "test.png"

    def test_scan_auto_categorizes_root_files(self, ws):
        ws.init_workspace()
        # Image in root → auto-detect as sprite
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        img.save(str(ws.inbox / "small_sprite.png"))
        found = ws.scan_inbox()
        assert len(found["sprites"]) == 1

    def test_get_output_path(self, ws):
        path = ws.get_output_path("textures", "test_texture.png")
        assert "output" in str(path)
        assert "textures" in str(path)
        assert path.parent.is_dir()

    def test_summary_no_workspace(self, ws):
        summary = ws.summary()
        assert "not initialized" in summary

    def test_summary_with_workspace(self, ws):
        ws.init_workspace()
        summary = ws.summary()
        assert "0 file(s) pending" in summary


# ── CLI Texture Command Tests ────────────────────────────────────────────────

class TestTextureCLI:
    def test_list_presets(self, capsys):
        from mathart.evolution.cli import main
        main(["texture"])
        captured = capsys.readouterr()
        assert "terrain" in captured.out
        assert "lava" in captured.out
        assert "clouds" in captured.out

    def test_generate_single(self, tmp_path, monkeypatch):
        from mathart.evolution.cli import main
        # Create minimal project structure
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "knowledge").mkdir()
        monkeypatch.chdir(tmp_path)
        out = str(tmp_path / "test_out.png")
        main(["texture", "terrain", "--size", "32", "-o", out])
        assert Path(out).exists()
        img = Image.open(out)
        assert img.size == (32, 32)

    def test_generate_all(self, tmp_path, monkeypatch):
        from mathart.evolution.cli import main
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "knowledge").mkdir()
        monkeypatch.chdir(tmp_path)
        main(["texture", "--all", "--size", "16"])
        tex_dir = tmp_path / "output" / "textures"
        assert tex_dir.exists()
        pngs = list(tex_dir.glob("*.png"))
        assert len(pngs) == 6  # All 6 presets


class TestInitWorkspaceCLI:
    def test_init_workspace_command(self, tmp_path, monkeypatch, capsys):
        from mathart.evolution.cli import main
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "knowledge").mkdir()
        monkeypatch.chdir(tmp_path)
        main(["init-workspace"])
        captured = capsys.readouterr()
        assert "Workspace initialized" in captured.out
        assert (tmp_path / "inbox" / "sprites").is_dir()
        assert (tmp_path / "output" / "textures").is_dir()

    def test_scan_empty(self, tmp_path, monkeypatch, capsys):
        from mathart.evolution.cli import main
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "knowledge").mkdir()
        monkeypatch.chdir(tmp_path)
        main(["init-workspace"])
        main(["scan"])
        captured = capsys.readouterr()
        assert "empty" in captured.out or "nothing to process" in captured.out
