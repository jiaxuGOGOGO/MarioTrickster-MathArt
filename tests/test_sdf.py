"""Tests for SDF module."""
import numpy as np
import pytest
from PIL import Image
from mathart.sdf.primitives import circle, box, triangle, star, ring, segment
from mathart.sdf.operations import union, intersection, subtraction, smooth_union, rotate, translate
from mathart.sdf.renderer import render_sdf, render_spritesheet
from mathart.sdf.effects import spike_sdf, flame_sdf, saw_blade_sdf, glow_sdf, electric_arc_sdf
from mathart.oklab.palette import PaletteGenerator


class TestPrimitives:
    @pytest.mark.unit
    def test_circle_center(self):
        c = circle(0, 0, 0.5)
        x = np.array([0.0])
        y = np.array([0.0])
        assert c(x, y)[0] < 0, "Center should be inside"

    @pytest.mark.unit
    def test_circle_outside(self):
        c = circle(0, 0, 0.5)
        x = np.array([1.0])
        y = np.array([0.0])
        assert c(x, y)[0] > 0, "Far point should be outside"

    @pytest.mark.unit
    def test_box_inside(self):
        b = box(0, 0, 0.5, 0.5)
        x = np.array([0.0])
        y = np.array([0.0])
        assert b(x, y)[0] < 0

    @pytest.mark.unit
    def test_box_corner(self):
        b = box(0, 0, 0.5, 0.5)
        x = np.array([0.5])
        y = np.array([0.5])
        assert abs(b(x, y)[0]) < 0.01, "Corner should be on boundary"

    @pytest.mark.unit
    def test_triangle_inside(self):
        t = triangle(0, -0.5, -0.5, 0.5, 0.5, 0.5)
        x = np.array([0.0])
        y = np.array([0.0])
        assert t(x, y)[0] < 0

    @pytest.mark.unit
    def test_star_center(self):
        s = star(0, 0, 0.5, 0.2, 5)
        x = np.array([0.0])
        y = np.array([0.0])
        assert s(x, y)[0] < 0

    @pytest.mark.unit
    def test_ring_center_outside(self):
        r = ring(0, 0, 0.4, 0.1)
        x = np.array([0.0])
        y = np.array([0.0])
        assert r(x, y)[0] > 0, "Center of ring should be outside"


class TestOperations:
    @pytest.mark.unit
    def test_union(self):
        c1 = circle(-0.3, 0, 0.3)
        c2 = circle(0.3, 0, 0.3)
        u = union(c1, c2)
        x = np.array([0.0])
        y = np.array([0.0])
        assert u(x, y)[0] <= 0, "Union center should be inside or on boundary"

    @pytest.mark.unit
    def test_intersection(self):
        c1 = circle(-0.3, 0, 0.3)
        c2 = circle(0.3, 0, 0.3)
        i = intersection(c1, c2)
        x = np.array([0.0])
        y = np.array([0.0])
        # Intersection at center should be inside both
        d = i(x, y)[0]
        assert d > -0.1  # Barely inside or outside

    @pytest.mark.unit
    def test_subtraction(self):
        big = circle(0, 0, 0.5)
        small = circle(0, 0, 0.2)
        s = subtraction(big, small)
        x = np.array([0.0])
        y = np.array([0.0])
        assert s(x, y)[0] > 0, "Center should be subtracted"

    @pytest.mark.unit
    def test_rotate(self):
        b = box(0.3, 0, 0.1, 0.1)
        r = rotate(b, np.pi / 2)
        x = np.array([0.0])
        y = np.array([0.3])
        assert r(x, y)[0] < 0.05


class TestRenderer:
    @pytest.mark.unit
    def test_render_basic(self):
        c = circle(0, 0, 0.5)
        img = render_sdf(c, 32, 32)
        assert img.mode == "RGBA"
        assert img.size == (32, 32)

    @pytest.mark.unit
    def test_render_transparent_bg(self):
        c = circle(0, 0, 0.5)
        img = render_sdf(c, 32, 32, bg_transparent=True)
        arr = np.array(img)
        # Corners should be transparent
        assert arr[0, 0, 3] == 0

    @pytest.mark.unit
    def test_render_with_palette(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=6)
        c = circle(0, 0, 0.5)
        img = render_sdf(c, 32, 32, palette=pal)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_spritesheet(self):
        def animated(x, y, t):
            return circle(0, 0, 0.3 + 0.1 * np.sin(2 * np.pi * t))(x, y)
        sheet = render_spritesheet(animated, frames=8, width=32, height=32)
        assert sheet.size == (256, 32)
        assert sheet.mode == "RGBA"


class TestEffects:
    @pytest.mark.unit
    def test_spike_sdf(self):
        s = spike_sdf()
        x = np.array([0.0])
        y = np.array([0.0])
        d = s(x, y)
        assert isinstance(d, np.ndarray)

    @pytest.mark.unit
    def test_flame_animated(self):
        for t in [0.0, 0.25, 0.5, 0.75]:
            f = flame_sdf(t=t)
            x = np.array([0.0])
            y = np.array([0.0])
            d = f(x, y)
            assert isinstance(d, np.ndarray)

    @pytest.mark.unit
    def test_saw_blade(self):
        for t in [0.0, 0.5]:
            s = saw_blade_sdf(t=t)
            x = np.array([0.0])
            y = np.array([0.0])
            d = s(x, y)
            assert isinstance(d, np.ndarray)

    @pytest.mark.unit
    def test_all_effects_renderable(self):
        """All effects should produce valid 32x32 RGBA sprites."""
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=6)

        # Static
        img = render_sdf(spike_sdf(), 32, 32, pal)
        assert img.size == (32, 32) and img.mode == "RGBA"

        # Animated
        for factory in [flame_sdf, saw_blade_sdf, glow_sdf, electric_arc_sdf]:
            def anim(x, y, t, _f=factory):
                return _f(t=t)(x, y)
            sheet = render_spritesheet(anim, 8, 32, 32, pal)
            assert sheet.size == (256, 32) and sheet.mode == "RGBA"
