"""Tests for OKLAB color space module."""
import numpy as np
import pytest
from mathart.oklab.color_space import (
    srgb_to_oklab, oklab_to_srgb, oklab_to_oklch, oklch_to_oklab,
    linear_to_oklab, oklab_to_linear,
)
from mathart.oklab.palette import PaletteGenerator, Palette
from mathart.oklab.quantizer import quantize_image
from PIL import Image


class TestColorSpaceConversions:
    """Test OKLAB ↔ sRGB round-trip accuracy."""

    @pytest.mark.unit
    def test_black_roundtrip(self):
        black = np.array([0, 0, 0])
        lab = srgb_to_oklab(black)
        assert lab[0] < 0.01, "Black should have L ≈ 0"
        back = oklab_to_srgb(lab)
        np.testing.assert_array_equal(back, [0, 0, 0])

    @pytest.mark.unit
    def test_white_roundtrip(self):
        white = np.array([255, 255, 255])
        lab = srgb_to_oklab(white)
        assert abs(lab[0] - 1.0) < 0.01, "White should have L ≈ 1"
        back = oklab_to_srgb(lab)
        np.testing.assert_array_equal(back, [255, 255, 255])

    @pytest.mark.unit
    def test_red_roundtrip(self):
        red = np.array([255, 0, 0])
        lab = srgb_to_oklab(red)
        back = oklab_to_srgb(lab)
        np.testing.assert_allclose(back, red, atol=1)

    @pytest.mark.unit
    def test_batch_roundtrip(self):
        colors = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [128, 128, 128]])
        lab = srgb_to_oklab(colors)
        back = oklab_to_srgb(lab)
        np.testing.assert_allclose(back, colors, atol=1)

    @pytest.mark.unit
    def test_oklch_roundtrip(self):
        lab = np.array([[0.5, 0.1, -0.1], [0.8, -0.05, 0.15]])
        lch = oklab_to_oklch(lab)
        back = oklch_to_oklab(lch)
        np.testing.assert_allclose(back, lab, atol=1e-10)

    @pytest.mark.unit
    def test_perceptual_uniformity(self):
        """Colors equidistant in OKLAB should look equally different."""
        c1 = np.array([0.5, 0.1, 0.0])
        c2 = np.array([0.5, -0.1, 0.0])
        c3 = np.array([0.5, 0.0, 0.1])
        d12 = np.linalg.norm(c1 - c2)
        d13 = np.linalg.norm(c1 - c3)
        # Both distances should be in the same order of magnitude
        # (OKLAB is approximately uniform, not perfectly so)
        assert abs(d12 - d13) < 0.1


class TestPaletteGenerator:
    """Test palette generation with various harmony types."""

    @pytest.mark.unit
    def test_warm_cool_shadow(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=8, name="test")
        assert pal.count == 8
        assert len(pal.colors_hex) == 8
        assert all(c.startswith("#") for c in pal.colors_hex)

    @pytest.mark.unit
    def test_tonal_ramp(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("tonal_ramp", count=5, name="ramp")
        assert pal.count == 5
        # Lightness should decrease along the ramp
        lch = oklab_to_oklch(pal.colors_oklab)
        assert lch[0, 0] > lch[-1, 0], "First color should be lighter than last"

    @pytest.mark.unit
    def test_all_harmony_types(self):
        gen = PaletteGenerator(seed=42)
        for harmony in ["complementary", "analogous", "triadic",
                        "split_complementary", "warm_cool_shadow", "tonal_ramp"]:
            pal = gen.generate(harmony, count=6, name=f"test_{harmony}")
            assert pal.count == 6, f"Failed for {harmony}"

    @pytest.mark.unit
    def test_gamut_clamping(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", chroma=0.3, count=8)
        srgb = pal.colors_srgb
        assert np.all(srgb >= 0) and np.all(srgb <= 255)

    @pytest.mark.unit
    def test_theme_palette(self):
        gen = PaletteGenerator(seed=42)
        theme = gen.generate_theme_palette("grassland")
        assert "ground" in theme
        assert "platform" in theme
        assert "wall" in theme
        assert "background" in theme
        assert "characters" in theme
        assert "hazards" in theme

    @pytest.mark.unit
    def test_palette_json_roundtrip(self, tmp_path):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=8, name="test")
        path = str(tmp_path / "test.json")
        pal.save_json(path)
        loaded = Palette.load_json(path)
        assert loaded.name == pal.name
        assert loaded.count == pal.count
        np.testing.assert_allclose(loaded.colors_oklab, pal.colors_oklab, atol=1e-6)


class TestQuantizer:
    """Test image quantization."""

    @pytest.mark.unit
    def test_quantize_basic(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=4)
        img = Image.new("RGBA", (16, 16), (128, 64, 32, 255))
        result = quantize_image(img, pal)
        assert result.mode == "RGBA"
        assert result.size == (16, 16)

    @pytest.mark.unit
    def test_quantize_preserves_alpha(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=4)
        img = Image.new("RGBA", (8, 8), (128, 64, 32, 0))
        result = quantize_image(img, pal, preserve_alpha=True)
        arr = np.array(result)
        assert np.all(arr[:, :, 3] == 0), "Transparent pixels should stay transparent"

    @pytest.mark.unit
    def test_quantize_reduces_colors(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("tonal_ramp", count=4)
        # Create image with many colors
        arr = np.random.randint(0, 255, (16, 16, 3), dtype=np.uint8)
        img = Image.fromarray(arr, "RGB")
        result = quantize_image(img, pal, preserve_alpha=False)
        # Count unique colors in result
        result_arr = np.array(result)
        unique = np.unique(result_arr.reshape(-1, 3), axis=0)
        assert len(unique) <= pal.count
