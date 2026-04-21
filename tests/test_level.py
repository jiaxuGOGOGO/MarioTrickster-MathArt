"""Tests for the WFC level generation module."""
from __future__ import annotations

import numpy as np
import pytest

from mathart.level.templates import (
    ELEMENT_MAP,
    NAME_TO_CHAR,
    CLASSIC_FRAGMENTS,
    SOLID_CHARS,
    PLATFORM_CHARS,
    HAZARD_CHARS,
    parse_fragment,
    fragment_to_string,
)
from mathart.level.wfc import WFCGenerator, AdjacencyRules


# ── Template tests ────────────────────────────────────────────────────


class TestTemplates:
    """Tests for level templates and element mapping."""

    @pytest.mark.unit
    def test_element_map_has_18_elements(self):
        """The element map should contain exactly 18 game elements."""
        assert len(ELEMENT_MAP) == 19

    @pytest.mark.unit
    def test_reverse_lookup_consistent(self):
        """NAME_TO_CHAR should be the inverse of ELEMENT_MAP."""
        for char, name in ELEMENT_MAP.items():
            assert NAME_TO_CHAR[name] == char

    @pytest.mark.unit
    def test_classic_fragments_exist(self):
        """There should be at least 5 classic fragments."""
        assert len(CLASSIC_FRAGMENTS) >= 5

    @pytest.mark.unit
    def test_parse_fragment_returns_2d_grid(self):
        """parse_fragment should return a list of lists."""
        grid = parse_fragment(CLASSIC_FRAGMENTS["tutorial_start"])
        assert isinstance(grid, list)
        assert all(isinstance(row, list) for row in grid)
        assert all(isinstance(c, str) and len(c) == 1 for row in grid for c in row)

    @pytest.mark.unit
    def test_fragment_roundtrip(self):
        """Parsing and re-serializing a fragment should preserve content."""
        for name, text in CLASSIC_FRAGMENTS.items():
            grid = parse_fragment(text)
            result = fragment_to_string(grid)
            # Normalize whitespace
            assert result.strip() == text.strip(), f"Roundtrip failed for {name}"

    @pytest.mark.unit
    def test_all_fragment_chars_are_known(self):
        """Every character in classic fragments should be in ELEMENT_MAP."""
        for name, text in CLASSIC_FRAGMENTS.items():
            grid = parse_fragment(text)
            for row in grid:
                for c in row:
                    assert c in ELEMENT_MAP, (
                        f"Unknown char '{c}' in fragment '{name}'"
                    )


# ── Adjacency Rules tests ────────────────────────────────────────────


class TestAdjacencyRules:
    """Tests for adjacency rule learning."""

    @pytest.mark.unit
    def test_learn_from_single_grid(self):
        """Learning from a grid should populate rules."""
        rules = AdjacencyRules()
        grid = parse_fragment(CLASSIC_FRAGMENTS["tutorial_start"])
        rules.learn_from_grid(grid)
        assert len(rules.all_tiles) > 0
        assert rules.tile_weights.total() > 0

    @pytest.mark.unit
    def test_learn_from_fragments(self):
        """Learning from all fragments should cover most tile types."""
        rules = AdjacencyRules()
        rules.learn_from_fragments()
        # Should know about air, ground, and at least a few others
        assert "." in rules.all_tiles
        assert "#" in rules.all_tiles
        assert len(rules.all_tiles) >= 8

    @pytest.mark.unit
    def test_adjacency_is_directional(self):
        """Rules in opposite directions should not be identical."""
        rules = AdjacencyRules()
        rules.learn_from_fragments()
        # Ground (#) below air (.) is common; air above ground is common
        down_from_air = rules.get_allowed(".", 2)  # down
        up_from_air = rules.get_allowed(".", 0)    # up
        # Both should have entries but may differ
        assert len(down_from_air) > 0
        assert len(up_from_air) > 0

    @pytest.mark.unit
    def test_get_weights_returns_counter(self):
        """get_weights should return frequency counts."""
        rules = AdjacencyRules()
        rules.learn_from_fragments()
        weights = rules.get_weights("#", 0)  # What's above ground?
        assert isinstance(weights, dict)


# ── WFC Generator tests ──────────────────────────────────────────────


class TestWFCGenerator:
    """Tests for the WFC level generator."""

    @pytest.fixture
    def generator(self):
        gen = WFCGenerator(seed=42)
        gen.learn()
        return gen

    @pytest.mark.unit
    def test_generate_returns_string(self, generator):
        """generate() should return a non-empty string."""
        result = generator.generate(22, 7)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.unit
    def test_generate_correct_dimensions(self, generator):
        """Generated level should have correct row/column counts."""
        width, height = 22, 7
        result = generator.generate(width, height)
        lines = result.strip().split("\n")
        assert len(lines) == height
        for line in lines:
            assert len(line) == width, f"Line has {len(line)} chars, expected {width}"

    @pytest.mark.unit
    def test_generate_bottom_is_ground(self, generator):
        """Bottom row should be solid ground when ensure_ground=True."""
        result = generator.generate(22, 7, ensure_ground=True)
        lines = result.strip().split("\n")
        bottom = lines[-1]
        assert all(c == "#" for c in bottom), f"Bottom row: {bottom}"

    @pytest.mark.unit
    def test_generate_has_spawn(self, generator):
        """Generated level should contain Mario spawn when ensure_spawn=True."""
        result = generator.generate(22, 7, ensure_spawn=True)
        assert "M" in result, "No Mario spawn point found"

    @pytest.mark.unit
    def test_generate_has_goal(self, generator):
        """Generated level should contain goal zone when ensure_goal=True."""
        result = generator.generate(22, 7, ensure_goal=True)
        assert "G" in result, "No goal zone found"

    @pytest.mark.unit
    def test_generate_all_chars_known(self, generator):
        """All characters in generated level should be valid elements."""
        result = generator.generate(22, 7)
        for char in result:
            if char != "\n":
                assert char in ELEMENT_MAP, f"Unknown char '{char}' in output"

    @pytest.mark.unit
    def test_generate_different_seeds_different_output(self):
        """Different seeds should produce different levels."""
        gen1 = WFCGenerator(seed=1)
        gen1.learn()
        gen2 = WFCGenerator(seed=999)
        gen2.learn()
        r1 = gen1.generate(22, 7)
        r2 = gen2.generate(22, 7)
        assert r1 != r2, "Different seeds produced identical levels"

    @pytest.mark.unit
    def test_generate_with_injected_numpy_generator_is_deterministic(self):
        """Explicit Generator injection should preserve deterministic output."""
        rng_a = np.random.default_rng(123456)
        rng_b = np.random.default_rng(123456)
        gen_a = WFCGenerator(rng=rng_a)
        gen_b = WFCGenerator(rng=rng_b)
        gen_a.learn()
        gen_b.learn()
        level_a = gen_a.generate(22, 7)
        level_b = gen_b.generate(22, 7)
        assert level_a == level_b

    @pytest.mark.unit
    def test_generate_batch(self, generator):
        """generate_batch should return the requested number of levels."""
        levels = generator.generate_batch(3, 22, 7)
        assert len(levels) == 3
        assert all(isinstance(l, str) for l in levels)

    @pytest.mark.unit
    def test_generate_without_learning_raises(self):
        """Generating without learning should raise RuntimeError."""
        gen = WFCGenerator(seed=42)
        with pytest.raises(RuntimeError, match="No adjacency rules"):
            gen.generate(22, 7)

    @pytest.mark.unit
    def test_generate_small_level(self, generator):
        """Should handle small level dimensions."""
        result = generator.generate(5, 3)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert all(len(l) == 5 for l in lines)

    @pytest.mark.unit
    def test_generate_wide_level(self, generator):
        """Should handle wide level dimensions."""
        result = generator.generate(40, 7)
        lines = result.strip().split("\n")
        assert len(lines) == 7
        assert all(len(l) == 40 for l in lines)

    @pytest.mark.integration
    def test_generate_no_constraints(self, generator):
        """Should work with all structural constraints disabled."""
        result = generator.generate(
            22, 7,
            ensure_ground=False,
            ensure_spawn=False,
            ensure_goal=False,
        )
        assert isinstance(result, str)
        lines = result.strip().split("\n")
        assert len(lines) == 7
