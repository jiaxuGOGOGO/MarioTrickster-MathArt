"""Tests for the L-System plant generation module."""
from __future__ import annotations

import pytest
from PIL import Image

from mathart.sdf.lsystem import LSystem, LSystemRule, PlantPresets, Segment


# ── Core L-System tests ──────────────────────────────────────────────


class TestLSystem:
    """Tests for the L-System core engine."""

    @pytest.mark.unit
    def test_iterate_identity(self):
        """With no rules, iteration should return the axiom unchanged."""
        ls = LSystem(axiom="F", rules=[])
        result = ls.iterate(3)
        assert result == "F"

    @pytest.mark.unit
    def test_iterate_simple_rule(self):
        """A simple rule should expand the axiom."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "F+F")],
        )
        result = ls.iterate(1)
        assert result == "F+F"
        result2 = ls.iterate(2)
        assert result2 == "F+F+F+F"

    @pytest.mark.unit
    def test_iterate_preserves_non_rule_chars(self):
        """Characters without rules should pass through unchanged."""
        ls = LSystem(
            axiom="F+G",
            rules=[LSystemRule("F", "FF")],
        )
        result = ls.iterate(1)
        assert result == "FF+G"

    @pytest.mark.unit
    def test_iterate_multiple_rules(self):
        """Multiple rules should all be applied."""
        ls = LSystem(
            axiom="AB",
            rules=[
                LSystemRule("A", "AB"),
                LSystemRule("B", "A"),
            ],
        )
        result = ls.iterate(1)
        assert result == "ABA"

    @pytest.mark.unit
    def test_iterate_zero_iterations(self):
        """Zero iterations should return the axiom."""
        ls = LSystem(axiom="F[+F][-F]")
        result = ls.iterate(0)
        assert result == "F[+F][-F]"

    @pytest.mark.unit
    def test_interpret_single_segment(self):
        """Interpreting 'F' should produce one segment."""
        ls = LSystem(axiom="F", length=5.0)
        segments = ls.interpret("F")
        assert len(segments) == 1
        assert segments[0].segment_type == "trunk"

    @pytest.mark.unit
    def test_interpret_branching(self):
        """Branching with [] should create separate branches."""
        ls = LSystem(axiom="F", angle=90.0, length=5.0)
        segments = ls.interpret("F[+F][-F]")
        # Should have 3 F segments
        f_segments = [s for s in segments if s.segment_type in ("trunk", "branch")]
        assert len(f_segments) == 3

    @pytest.mark.unit
    def test_interpret_leaf(self):
        """'L' should create a leaf segment."""
        ls = LSystem()
        segments = ls.interpret("FL")
        leaf_segs = [s for s in segments if s.segment_type == "leaf"]
        assert len(leaf_segs) == 1

    @pytest.mark.unit
    def test_interpret_flower(self):
        """'*' should create a flower segment."""
        ls = LSystem()
        segments = ls.interpret("F*")
        flower_segs = [s for s in segments if s.segment_type == "flower"]
        assert len(flower_segs) == 1

    @pytest.mark.unit
    def test_interpret_depth_tracking(self):
        """Depth should increase inside brackets."""
        ls = LSystem(length=5.0)
        segments = ls.interpret("F[F[F]]")
        depths = [s.depth for s in segments]
        assert 0 in depths
        assert 1 in depths
        assert 2 in depths

    @pytest.mark.unit
    def test_generate_produces_segments(self):
        """generate() should produce a non-empty list of segments."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "F[+F][-F]")],
        )
        segments = ls.generate(iterations=2)
        assert len(segments) > 0
        assert all(isinstance(s, Segment) for s in segments)

    @pytest.mark.unit
    def test_deterministic_with_seed(self):
        """Same seed should produce identical results."""
        ls1 = LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[+F]F", probability=0.5),
                LSystemRule("F", "F[-F]F", probability=0.5),
            ],
            seed=42,
        )
        ls2 = LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[+F]F", probability=0.5),
                LSystemRule("F", "F[-F]F", probability=0.5),
            ],
            seed=42,
        )
        s1 = ls1.iterate(3)
        s2 = ls2.iterate(3)
        assert s1 == s2


# ── Rendering tests ──────────────────────────────────────────────────


class TestLSystemRendering:
    """Tests for L-System rendering to images."""

    @pytest.mark.unit
    def test_render_returns_image(self):
        """render() should return a PIL Image."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "F[+F][-F]")],
        )
        ls.generate(iterations=2)
        img = ls.render(32, 32)
        assert isinstance(img, Image.Image)
        assert img.size == (32, 32)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_without_generate_raises(self):
        """render() without generate() should raise RuntimeError."""
        ls = LSystem()
        with pytest.raises(RuntimeError, match="No segments"):
            ls.render(32, 32)

    @pytest.mark.unit
    def test_render_custom_palette(self):
        """render() should accept a custom palette."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "F[+FL][-FL]")],
        )
        ls.generate(iterations=2)
        palette = [
            (255, 0, 0, 255),
            (0, 255, 0, 255),
            (0, 0, 255, 255),
            (255, 255, 0, 255),
        ]
        img = ls.render(32, 32, palette=palette)
        assert img.size == (32, 32)

    @pytest.mark.unit
    def test_render_has_non_transparent_pixels(self):
        """Rendered image should contain non-transparent pixels."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "FF[+F][-F]")],
        )
        ls.generate(iterations=3)
        img = ls.render(64, 64)
        import numpy as np
        arr = np.array(img)
        alpha = arr[:, :, 3]
        assert alpha.max() > 0, "Image is completely transparent"

    @pytest.mark.unit
    def test_render_various_sizes(self):
        """render() should work with different image sizes."""
        ls = LSystem(
            axiom="F",
            rules=[LSystemRule("F", "F[+F][-F]")],
        )
        ls.generate(iterations=2)
        for size in [(16, 16), (32, 32), (64, 64), (128, 128)]:
            img = ls.render(*size)
            assert img.size == size


# ── Plant Presets tests ──────────────────────────────────────────────


class TestPlantPresets:
    """Tests for pre-configured plant presets."""

    @pytest.mark.unit
    def test_all_presets_exist(self):
        """all_presets() should return at least 5 presets."""
        presets = PlantPresets.all_presets()
        assert len(presets) >= 5

    @pytest.mark.unit
    def test_all_presets_are_lsystem(self):
        """All presets should be LSystem instances."""
        for name, ls in PlantPresets.all_presets().items():
            assert isinstance(ls, LSystem), f"{name} is not an LSystem"

    @pytest.mark.unit
    def test_all_presets_generate(self):
        """All presets should generate segments without error."""
        for name, ls in PlantPresets.all_presets().items():
            segments = ls.generate(iterations=3)
            assert len(segments) > 0, f"{name} produced no segments"

    @pytest.mark.unit
    def test_all_presets_render(self):
        """All presets should render to valid images."""
        for name, ls in PlantPresets.all_presets().items():
            ls.generate(iterations=3)
            img = ls.render(32, 32)
            assert img.size == (32, 32), f"{name} render failed"
            assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_oak_tree_has_branches(self):
        """Oak tree should produce branching structure."""
        oak = PlantPresets.oak_tree(seed=42)
        segments = oak.generate(iterations=3)
        branch_segs = [s for s in segments if s.segment_type == "branch"]
        assert len(branch_segs) > 0, "Oak tree has no branches"

    @pytest.mark.unit
    def test_flower_plant_has_flowers(self):
        """Flower plant should produce flower segments."""
        plant = PlantPresets.flower_plant(seed=42)
        segments = plant.generate(iterations=3)
        flower_segs = [s for s in segments if s.segment_type == "flower"]
        assert len(flower_segs) > 0, "Flower plant has no flowers"

    @pytest.mark.unit
    def test_presets_with_seed_deterministic(self):
        """Presets with same seed should produce identical results."""
        oak1 = PlantPresets.oak_tree(seed=123)
        oak2 = PlantPresets.oak_tree(seed=123)
        s1 = oak1.generate(iterations=3)
        s2 = oak2.generate(iterations=3)
        assert len(s1) == len(s2)
        for a, b in zip(s1, s2):
            assert a.x0 == b.x0 and a.y0 == b.y0
