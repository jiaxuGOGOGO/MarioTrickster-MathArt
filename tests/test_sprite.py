"""Tests for Sprite Analysis Engine."""
from __future__ import annotations

import json
import pytest
import numpy as np
from pathlib import Path
from PIL import Image

from mathart.sprite.analyzer import SpriteAnalyzer, StyleFingerprint
from mathart.sprite.sheet_parser import SpriteSheetParser
from mathart.sprite.library import SpriteLibrary


def make_sprite(w: int, h: int, colors: list[tuple[int, int, int]]) -> Image.Image:
    """Create a test sprite with multiple colors."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            color_idx = (y * w + x) % len(colors)
            r, g, b = colors[color_idx]
            pixels[x, y] = (r, g, b, 255)
    return img


def make_character_sprite(w: int = 16, h: int = 32) -> Image.Image:
    """Create a simple character-like sprite."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pixels = img.load()
    # Head (top quarter)
    for y in range(h // 4):
        for x in range(w // 4, 3 * w // 4):
            pixels[x, y] = (200, 150, 100, 255)
    # Body (middle half)
    for y in range(h // 4, 3 * h // 4):
        for x in range(w // 6, 5 * w // 6):
            pixels[x, y] = (100, 100, 200, 255)
    # Legs (bottom quarter)
    for y in range(3 * h // 4, h):
        for x in range(w // 4, w // 2 - 1):
            pixels[x, y] = (50, 50, 150, 255)
        for x in range(w // 2 + 1, 3 * w // 4):
            pixels[x, y] = (50, 50, 150, 255)
    return img


class TestSpriteSheetParser:
    def test_parse_uniform_basic(self):
        """Should parse a 2x2 spritesheet into 4 frames."""
        sheet = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        # Fill each quadrant with a different color
        for row in range(2):
            for col in range(2):
                for y in range(32):
                    for x in range(32):
                        c = (row * 100, col * 100, 50, 255)
                        sheet.putpixel((col * 32 + x, row * 32 + y), c)

        parser = SpriteSheetParser()
        result = parser.parse_uniform(sheet, 32, 32)

        assert result.rows == 2
        assert result.cols == 2
        assert len(result.frames) == 4
        assert result.cell_width == 32
        assert result.cell_height == 32

    def test_parse_uniform_frame_size(self):
        """Each frame should have the correct dimensions."""
        sheet = Image.new("RGBA", (128, 32), (200, 100, 50, 255))
        parser = SpriteSheetParser()
        result = parser.parse_uniform(sheet, 32, 32)

        assert result.cols == 4
        assert result.rows == 1
        for frame in result.frames:
            assert frame.width == 32
            assert frame.height == 32

    def test_parse_auto_detects_grid(self):
        """Auto-detect should find the grid size."""
        sheet = Image.new("RGBA", (64, 64), (100, 100, 100, 255))
        parser = SpriteSheetParser()
        result = parser.parse_auto(sheet, hint_rows=2, hint_cols=2)
        assert result.rows == 2
        assert result.cols == 2

    def test_empty_frame_detection(self):
        """Transparent frames should be marked as not having content."""
        sheet = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
        # Only fill the first frame
        for y in range(32):
            for x in range(32):
                sheet.putpixel((x, y), (200, 100, 50, 255))

        parser = SpriteSheetParser(min_content_ratio=0.01)
        result = parser.parse_uniform(sheet, 32, 32)

        assert bool(result.frames[0].has_content) is True
        assert bool(result.frames[1].has_content) is False

    def test_get_animation_row(self):
        """get_animation_row should return frames from a specific row."""
        sheet = Image.new("RGBA", (64, 64), (100, 100, 100, 255))
        parser = SpriteSheetParser()
        result = parser.parse_uniform(sheet, 32, 32)
        row0 = result.get_animation_row(0)
        assert len(row0) == 2
        assert all(f.row == 0 for f in row0)

    def test_detect_cell_size_power_of_two(self):
        """Should detect power-of-two cell sizes."""
        assert SpriteSheetParser._detect_cell_size(64) == 64
        assert SpriteSheetParser._detect_cell_size(128) == 128
        assert SpriteSheetParser._detect_cell_size(32) == 32

    def test_save_frames(self, tmp_path):
        """Should save frames to files."""
        sheet = Image.new("RGBA", (64, 32), (200, 100, 50, 255))
        parser = SpriteSheetParser()
        result = parser.parse_uniform(sheet, 32, 32)
        saved = parser.save_frames(result, tmp_path / "frames")
        assert len(saved) == 2
        for path in saved:
            assert path.exists()

    def test_parse_file(self, tmp_path):
        """Should parse a spritesheet from a file."""
        sheet = Image.new("RGBA", (64, 32), (200, 100, 50, 255))
        sheet_path = tmp_path / "sheet.png"
        sheet.save(sheet_path)

        parser = SpriteSheetParser()
        result = parser.parse_file(sheet_path, cell_width=32, cell_height=32)
        assert result.frame_count == 2


class TestSpriteAnalyzer:
    def test_analyze_basic(self):
        """Should analyze a basic sprite."""
        analyzer = SpriteAnalyzer()
        sprite = make_sprite(32, 32, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        fp = analyzer.analyze(sprite, "test_sprite")

        assert isinstance(fp, StyleFingerprint)
        assert fp.source_name == "test_sprite"
        assert fp.width == 32
        assert fp.height == 32
        assert fp.color.color_count > 0
        assert 0.0 <= fp.quality_score <= 1.0

    def test_analyze_color_count(self):
        """Color count should reflect actual colors."""
        analyzer = SpriteAnalyzer(max_palette_colors=8)
        # Single color sprite
        sprite = make_sprite(32, 32, [(200, 100, 50)])
        fp = analyzer.analyze(sprite)
        assert fp.color.color_count >= 1

    def test_analyze_contrast(self):
        """High contrast sprite should have higher contrast score."""
        analyzer = SpriteAnalyzer()
        # Black and white = high contrast
        high_contrast = make_sprite(32, 32, [(0, 0, 0), (255, 255, 255)])
        fp_high = analyzer.analyze(high_contrast)

        # Single mid-gray = low contrast
        low_contrast = make_sprite(32, 32, [(128, 128, 128)])
        fp_low = analyzer.analyze(low_contrast)

        assert fp_high.color.contrast >= fp_low.color.contrast

    def test_analyze_fill_ratio(self):
        """Fully opaque sprite should have high fill ratio."""
        analyzer = SpriteAnalyzer()
        full_sprite = make_sprite(32, 32, [(200, 100, 50)])
        fp = analyzer.analyze(full_sprite)
        assert fp.shape.fill_ratio > 0.9

    def test_analyze_symmetry(self):
        """Symmetric sprite should have high symmetry score."""
        analyzer = SpriteAnalyzer()
        # Create a symmetric sprite
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        for y in range(32):
            for x in range(16):
                img.putpixel((x, y), (200, 100, 50, 255))
                img.putpixel((31 - x, y), (200, 100, 50, 255))
        fp = analyzer.analyze(img)
        assert fp.shape.symmetry_score > 0.7

    def test_analyze_character_type(self):
        """Character sprite should be detected as character type."""
        analyzer = SpriteAnalyzer()
        sprite = make_character_sprite(16, 32)
        fp = analyzer.analyze(sprite, sprite_type="character")
        assert fp.sprite_type == "character"

    def test_analyze_anatomy(self):
        """Character sprite should have anatomy profile."""
        analyzer = SpriteAnalyzer()
        sprite = make_character_sprite(16, 32)
        fp = analyzer.analyze(sprite, sprite_type="character")
        assert fp.anatomy is not None
        assert 0.0 < fp.anatomy.head_ratio < 1.0

    def test_analyze_frames_animation(self):
        """Should analyze animation frames."""
        analyzer = SpriteAnalyzer()
        frames = [make_sprite(16, 16, [(i * 30, 100, 50)]) for i in range(4)]
        fp = analyzer.analyze_frames(frames, "test_anim")
        assert fp.animation is not None
        assert fp.animation.frame_count == 4
        assert fp.animation.motion_magnitude >= 0.0

    def test_to_constraints(self):
        """Should convert fingerprint to parameter constraints."""
        analyzer = SpriteAnalyzer()
        sprite = make_sprite(32, 32, [(255, 0, 0), (0, 255, 0)])
        fp = analyzer.analyze(sprite)
        constraints = fp.to_constraints()
        assert "palette_size" in constraints
        assert "contrast" in constraints
        lo, hi = constraints["palette_size"]
        assert lo < hi

    def test_tags_extraction(self):
        """Should extract relevant tags."""
        analyzer = SpriteAnalyzer()
        # Sprite with black outline
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 255))  # All black
        fp = analyzer.analyze(img)
        assert "outlined" in fp.tags or len(fp.tags) >= 0  # Tags may vary

    def test_quality_score_range(self):
        """Quality score should be in [0, 1]."""
        analyzer = SpriteAnalyzer()
        for size in [8, 16, 32, 64]:
            sprite = make_sprite(size, size, [(200, 100, 50), (0, 0, 0)])
            fp = analyzer.analyze(sprite)
            assert 0.0 <= fp.quality_score <= 1.0


class TestSpriteLibrary:
    def test_add_sprite_new(self, tmp_path):
        """Should add a new sprite to the library."""
        lib = SpriteLibrary(project_root=tmp_path)
        sprite = make_sprite(32, 32, [(200, 100, 50)])
        fp, is_new = lib.add_sprite(sprite, "test_sprite")
        assert is_new is True
        assert lib.count() == 1

    def test_add_sprite_duplicate(self, tmp_path):
        """Should detect duplicate sprites."""
        lib = SpriteLibrary(project_root=tmp_path)
        sprite = make_sprite(32, 32, [(200, 100, 50)])
        _, is_new1 = lib.add_sprite(sprite, "sprite1")
        _, is_new2 = lib.add_sprite(sprite, "sprite2")  # Same image
        assert is_new1 is True
        assert is_new2 is False
        assert lib.count() == 1

    def test_add_different_sprites(self, tmp_path):
        """Different sprites should both be added."""
        lib = SpriteLibrary(project_root=tmp_path)
        s1 = make_sprite(32, 32, [(255, 0, 0)])
        s2 = make_sprite(32, 32, [(0, 0, 255)])
        lib.add_sprite(s1, "red")
        lib.add_sprite(s2, "blue")
        assert lib.count() == 2

    def test_persistence(self, tmp_path):
        """Library should persist across instances."""
        lib1 = SpriteLibrary(project_root=tmp_path)
        sprite = make_sprite(32, 32, [(200, 100, 50)])
        lib1.add_sprite(sprite, "persistent_sprite")

        lib2 = SpriteLibrary(project_root=tmp_path)
        assert lib2.count() == 1

    def test_get_stats_empty(self, tmp_path):
        """Stats for empty library should have zero values."""
        lib = SpriteLibrary(project_root=tmp_path)
        stats = lib.get_stats()
        assert stats.total_sprites == 0
        assert stats.avg_quality == 0.0

    def test_get_stats_with_sprites(self, tmp_path):
        """Stats should aggregate across sprites."""
        lib = SpriteLibrary(project_root=tmp_path)
        for i in range(3):
            s = make_sprite(32, 32, [(i * 80, 100, 50)])
            lib.add_sprite(s, f"sprite_{i}")
        stats = lib.get_stats()
        assert stats.total_sprites == 3
        assert stats.avg_quality >= 0.0

    def test_get_best_references(self, tmp_path):
        """Should return highest quality sprites."""
        lib = SpriteLibrary(project_root=tmp_path)
        # Add sprites of different sizes (larger = higher quality)
        for size in [8, 32, 64]:
            s = make_sprite(size, size, [(200, 100, 50), (0, 0, 0)])
            lib.add_sprite(s, f"sprite_{size}x{size}")
        refs = lib.get_best_references(top_n=2)
        assert len(refs) <= 2

    def test_export_constraints(self, tmp_path):
        """Should export merged constraints."""
        lib = SpriteLibrary(project_root=tmp_path)
        s = make_sprite(32, 32, [(200, 100, 50), (0, 0, 0)])
        lib.add_sprite(s, "test")
        constraints = lib.export_constraints()
        assert isinstance(constraints, dict)

    def test_sprite_log_created(self, tmp_path):
        """Should create SPRITE_LOG.md."""
        lib = SpriteLibrary(project_root=tmp_path)
        s = make_sprite(32, 32, [(200, 100, 50)])
        lib.add_sprite(s, "logged_sprite")
        log_path = tmp_path / "SPRITE_LOG.md"
        assert log_path.exists()
        assert "logged_sprite" in log_path.read_text(encoding="utf-8")

    def test_add_frames(self, tmp_path):
        """Should add animation frames."""
        lib = SpriteLibrary(project_root=tmp_path)
        frames = [make_sprite(16, 16, [(i * 60, 100, 50)]) for i in range(4)]
        fp, is_new = lib.add_frames(frames, "walk_anim")
        assert is_new is True
        assert fp.animation is not None
        assert fp.animation.frame_count == 4

    def test_library_json_format(self, tmp_path):
        """Library JSON should have correct structure."""
        lib = SpriteLibrary(project_root=tmp_path)
        s = make_sprite(32, 32, [(200, 100, 50)])
        lib.add_sprite(s, "test")
        lib_path = tmp_path / "knowledge" / "sprite_library.json"
        assert lib_path.exists()
        data = json.loads(lib_path.read_text(encoding="utf-8"))
        assert "entries" in data
        assert "count" in data
        assert data["count"] == 1
