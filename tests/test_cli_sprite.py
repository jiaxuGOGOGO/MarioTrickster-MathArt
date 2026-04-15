"""Tests for mathart-evolve add-sprite / add-sheet / sprites CLI commands
and the _try_widen_space bug fix.
"""
import numpy as np
import pytest
from PIL import Image


def _make_sprite(w=32, h=32, color=(200, 80, 80)):
    """Create a simple test sprite image."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[4:-4, 4:-4] = [*color, 255]
    return Image.fromarray(arr, mode="RGBA")


def _make_spritesheet(cols=4, rows=2, cell=32):
    """Create a simple test spritesheet."""
    w, h = cols * cell, rows * cell
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * cell + 4, r * cell + 4
            x1, y1 = (c + 1) * cell - 4, (r + 1) * cell - 4
            shade = 100 + (r * cols + c) * 15
            arr[y0:y1, x0:x1] = [shade, 80, 80, 255]
    return Image.fromarray(arr, mode="RGBA")


# ── CLI add-sprite tests ──────────────────────────────────────────────────────

@pytest.mark.unit
class TestAddSpriteCLI:
    def test_add_sprite_basic(self, tmp_path):
        """Test that add-sprite CLI adds a sprite to the library."""
        from mathart.evolution.cli import main

        # Create a test sprite file
        sprite = _make_sprite()
        sprite_path = tmp_path / "test_sprite.png"
        sprite.save(sprite_path)

        # Create a minimal project structure
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["add-sprite", str(sprite_path)])
        finally:
            os.chdir(old_cwd)

        # Verify library was created
        lib_json = tmp_path / "knowledge" / "sprite_library.json"
        assert lib_json.exists()

    def test_add_sprite_with_options(self, tmp_path):
        """Test add-sprite with --type, --name, --tags options."""
        from mathart.evolution.cli import main

        sprite = _make_sprite(color=(80, 200, 80))
        sprite_path = tmp_path / "mario_idle.png"
        sprite.save(sprite_path)

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main([
                "add-sprite", str(sprite_path),
                "--type", "character",
                "--name", "mario_idle",
                "--tags", "mario,idle,16x16",
            ])
        finally:
            os.chdir(old_cwd)

        lib_json = tmp_path / "knowledge" / "sprite_library.json"
        assert lib_json.exists()

        import json
        data = json.loads(lib_json.read_text())
        assert data["count"] == 1
        entry = data["entries"][0]
        assert entry["sprite_type"] in ("character", "unknown")  # May auto-detect
        assert entry["source_name"] == "mario_idle"

    def test_add_sprite_duplicate(self, tmp_path):
        """Test that adding the same sprite twice reports DUP."""
        from mathart.evolution.cli import main

        sprite = _make_sprite()
        sprite_path = tmp_path / "test_sprite.png"
        sprite.save(sprite_path)

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["add-sprite", str(sprite_path)])
            main(["add-sprite", str(sprite_path)])
        finally:
            os.chdir(old_cwd)

        import json
        lib_json = tmp_path / "knowledge" / "sprite_library.json"
        data = json.loads(lib_json.read_text())
        assert data["count"] == 1  # Still only 1 entry


@pytest.mark.unit
class TestAddSheetCLI:
    def test_add_sheet_auto(self, tmp_path):
        """Test add-sheet with auto cell-size detection."""
        from mathart.evolution.cli import main

        sheet = _make_spritesheet()
        sheet_path = tmp_path / "test_sheet.png"
        sheet.save(sheet_path)

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["add-sheet", str(sheet_path)])
        finally:
            os.chdir(old_cwd)

        lib_json = tmp_path / "knowledge" / "sprite_library.json"
        assert lib_json.exists()

    def test_add_sheet_with_cell_size(self, tmp_path):
        """Test add-sheet with explicit cell size."""
        from mathart.evolution.cli import main

        sheet = _make_spritesheet(cols=4, rows=2, cell=32)
        sheet_path = tmp_path / "test_sheet.png"
        sheet.save(sheet_path)

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["add-sheet", str(sheet_path), "--cell-size", "32x32"])
        finally:
            os.chdir(old_cwd)

        lib_json = tmp_path / "knowledge" / "sprite_library.json"
        assert lib_json.exists()


@pytest.mark.unit
class TestSpritesCLI:
    def test_sprites_empty(self, tmp_path):
        """Test sprites command with empty library."""
        from mathart.evolution.cli import main

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["sprites"])
        finally:
            os.chdir(old_cwd)

    def test_sprites_with_data(self, tmp_path):
        """Test sprites command with sprites in library."""
        from mathart.evolution.cli import main

        sprite = _make_sprite()
        sprite_path = tmp_path / "test_sprite.png"
        sprite.save(sprite_path)

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "knowledge").mkdir()

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            main(["add-sprite", str(sprite_path)])
            main(["sprites"])
        finally:
            os.chdir(old_cwd)


# ── _try_widen_space bug fix test ─────────────────────────────────────────────

@pytest.mark.unit
class TestWidenSpaceBugFix:
    def test_widen_space_uses_correct_attribute(self):
        """Verify _try_widen_space accesses optimizer.space (not optimizer._space)."""
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint
        from mathart.distill.optimizer import EvolutionaryOptimizer

        runner = InnerLoopRunner(verbose=False)
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(
            param_name="brightness", min_value=0.2, max_value=0.8, default_value=0.5
        ))

        optimizer = EvolutionaryOptimizer(space, population_size=5)

        # Before widening
        orig_min = space.constraints["brightness"].min_value
        orig_max = space.constraints["brightness"].max_value

        # This should NOT raise AttributeError anymore
        runner._try_widen_space(optimizer)

        # After widening, range should be wider
        new_min = space.constraints["brightness"].min_value
        new_max = space.constraints["brightness"].max_value
        assert new_min < orig_min, "min_value should decrease after widening"
        assert new_max > orig_max, "max_value should increase after widening"

    def test_widen_space_percentage(self):
        """Verify widening is approximately 15% (7.5% each side)."""
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint
        from mathart.distill.optimizer import EvolutionaryOptimizer

        runner = InnerLoopRunner(verbose=False)
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(
            param_name="x", min_value=0.0, max_value=1.0, default_value=0.5
        ))

        optimizer = EvolutionaryOptimizer(space, population_size=5)
        runner._try_widen_space(optimizer)

        # Original span = 1.0, 7.5% = 0.075
        assert abs(space.constraints["x"].min_value - (-0.075)) < 0.001
        assert abs(space.constraints["x"].max_value - 1.075) < 0.001
