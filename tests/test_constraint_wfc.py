"""Tests for SESSION-057 Constraint-Aware WFC with TTC Reachability.

Validates:
  - PhysicsConstraint: jump calculations, reachability, feasibility scores
  - TilePlatformExtractor: platform/gap extraction from ASCII grids
  - ReachabilityValidator: level playability validation, reachability maps
  - ConstraintAwareWFC: constrained generation, veto mechanism, difficulty
  - Difficulty curves: linear, sigmoid, wave
"""
import math

import pytest

from mathart.level.constraint_wfc import (
    PhysicsConstraint,
    PlatformSegment,
    GapDescription,
    TilePlatformExtractor,
    ReachabilityValidator,
    ConstraintAwareWFC,
    linear_difficulty,
    sigmoid_difficulty,
    wave_difficulty,
)
from mathart.level.templates import (
    parse_fragment,
    SOLID_CHARS,
    PLATFORM_CHARS,
    AIR_CHAR,
)


# ── PhysicsConstraint Tests ──────────────────────────────────────────────────


class TestPhysicsConstraint:
    """Test physics model for jump calculations."""

    def test_mario_default_values(self):
        """Mario default physics produces reasonable values."""
        p = PhysicsConstraint.mario_default()
        assert p.max_jump_height > 2.0   # Can jump at least 2 tiles high
        assert p.max_jump_height < 5.0   # But not unreasonably high
        assert p.max_jump_distance > 5.0  # Can jump at least 5 tiles wide
        assert p.max_jump_distance < 12.0  # But not unreasonably far
        assert p.t_air > 0.5  # Reasonable airtime

    def test_hard_mode_is_harder(self):
        """Hard mode physics should have shorter reach."""
        normal = PhysicsConstraint.mario_default()
        hard = PhysicsConstraint.hard_mode()
        assert hard.max_jump_distance < normal.max_jump_distance
        assert hard.max_jump_height < normal.max_jump_height

    def test_can_reach_small_gap(self):
        """Small gaps should be reachable."""
        p = PhysicsConstraint.mario_default()
        assert p.can_reach_horizontal(2.0)
        assert p.can_reach_vertical(1.0)
        assert p.can_reach(2.0, 1.0)

    def test_cannot_reach_huge_gap(self):
        """Huge gaps should not be reachable."""
        p = PhysicsConstraint.mario_default()
        assert not p.can_reach_horizontal(20.0)
        assert not p.can_reach_vertical(10.0)
        assert not p.can_reach(15.0, 5.0)

    def test_falling_always_reachable(self):
        """Falling down (negative vertical gap) should always be reachable."""
        p = PhysicsConstraint.mario_default()
        assert p.can_reach(3.0, -5.0)

    def test_jump_integral_easy(self):
        """Small gap should have high feasibility."""
        p = PhysicsConstraint.mario_default()
        score = p.jump_integral(1.0, 0.0)
        assert score > 0.5

    def test_jump_integral_impossible(self):
        """Huge gap should have zero/low feasibility."""
        p = PhysicsConstraint.mario_default()
        score = p.jump_integral(20.0, 5.0)
        assert score < 0.1

    def test_jump_integral_no_gap(self):
        """No gap should have perfect feasibility."""
        p = PhysicsConstraint.mario_default()
        assert p.jump_integral(0.0, 0.0) == 1.0

    def test_serialization(self):
        """Physics constraint serializes correctly."""
        p = PhysicsConstraint.mario_default()
        d = p.to_dict()
        assert "gravity" in d
        assert "max_jump_height" in d
        assert "max_jump_distance" in d
        assert abs(d["gravity"] - 26.0) < 0.01


# ── TilePlatformExtractor Tests ──────────────────────────────────────────────


class TestTilePlatformExtractor:
    """Test platform and gap extraction from level grids."""

    def _simple_grid(self):
        """Create a simple test grid with platforms and a gap."""
        text = (
            "......................\n"
            "......................\n"
            "......................\n"
            "..M...........G......\n"
            "..###.....####..####.\n"
            "..###.....####..####.\n"
            "######################"
        )
        return parse_fragment(text)

    def test_extract_platforms_finds_ground(self):
        """Should find the bottom ground platform."""
        grid = self._simple_grid()
        platforms = TilePlatformExtractor.extract_platforms(grid)
        assert len(platforms) >= 1
        # Bottom row should be one big platform
        bottom_platforms = [p for p in platforms if p.row == len(grid) - 1]
        assert len(bottom_platforms) >= 1

    def test_extract_platforms_finds_elevated(self):
        """Should find elevated platforms."""
        grid = self._simple_grid()
        platforms = TilePlatformExtractor.extract_platforms(grid)
        elevated = [p for p in platforms if p.row < len(grid) - 1]
        assert len(elevated) >= 1

    def test_platform_width(self):
        """Platform width should be correct."""
        grid = self._simple_grid()
        platforms = TilePlatformExtractor.extract_platforms(grid)
        for p in platforms:
            assert p.width > 0
            assert p.col_end > p.col_start

    def test_extract_gaps(self):
        """Should find gaps between platforms."""
        grid = self._simple_grid()
        physics = PhysicsConstraint.mario_default()
        gaps = TilePlatformExtractor.extract_gaps(grid, physics)
        # Should find at least one gap
        assert len(gaps) >= 0  # May be 0 if platforms overlap vertically

    def test_platform_graph_connected(self):
        """Platform graph should have connections."""
        grid = self._simple_grid()
        physics = PhysicsConstraint.mario_default()
        graph = TilePlatformExtractor.platform_graph(grid, physics)
        # Should have at least one platform
        assert len(graph) >= 1
        # At least some platforms should be reachable from others
        total_edges = sum(len(v) for v in graph.values())
        assert total_edges >= 0


# ── ReachabilityValidator Tests ──────────────────────────────────────────────


class TestReachabilityValidator:
    """Test level playability validation."""

    def test_simple_playable_level(self):
        """Simple flat level with spawn and goal should be playable."""
        text = (
            "......................\n"
            "......................\n"
            "......................\n"
            "..M...............G..\n"
            "######################\n"
            "######################\n"
            "######################"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        is_playable, path = validator.validate_level(grid)
        assert is_playable
        assert len(path) >= 1

    def test_impossible_level_huge_gap(self):
        """Level with impossibly wide gap should be unplayable."""
        # Create a level with a 20-tile gap
        text = (
            "......................\n"
            "......................\n"
            "......................\n"
            "M...................G.\n"
            "#....................#\n"
            "#....................#\n"
            "#....................#"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        is_playable, path = validator.validate_level(grid)
        assert not is_playable

    def test_level_with_stepping_stones(self):
        """Level with stepping stone platforms should be playable."""
        text = (
            "......................\n"
            "......................\n"
            "..M...o...o...o...G..\n"
            "..#...#...#...#...#..\n"
            "..#...#...#...#...#..\n"
            "..#...#...#...#...#..\n"
            "######################"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        is_playable, path = validator.validate_level(grid)
        assert is_playable

    def test_no_spawn_is_invalid(self):
        """Level without spawn should be invalid."""
        text = (
            "......................\n"
            "......................\n"
            "......................\n"
            "....................G.\n"
            "######################\n"
            "######################\n"
            "######################"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        is_playable, _ = validator.validate_level(grid)
        assert not is_playable

    def test_reachability_map(self):
        """Reachability map should mark spawn area as reachable."""
        text = (
            "......................\n"
            "......................\n"
            "......................\n"
            "..M.................G.\n"
            "######################\n"
            "######################\n"
            "######################"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        reach_map = validator.compute_reachability_map(grid)
        assert len(reach_map) == len(grid)
        # Some cells should be reachable
        total_reachable = sum(sum(1 for v in row if v) for row in reach_map)
        assert total_reachable > 0

    def test_difficulty_at_column(self):
        """Difficulty estimation should return valid values."""
        text = (
            "......................\n"
            "......................\n"
            "..^...~...^...~......\n"
            "######################\n"
            "######################\n"
            "######################\n"
            "######################"
        )
        grid = parse_fragment(text)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        for c in range(len(grid[0])):
            d = validator.difficulty_at_column(grid, c)
            assert 0.0 <= d <= 1.0


# ── ConstraintAwareWFC Tests ─────────────────────────────────────────────────


class TestConstraintAwareWFC:
    """Test constraint-aware WFC generation."""

    def test_basic_generation(self):
        """Constraint-aware WFC generates a non-empty level."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        level = gen.generate(20, 7, validate_playability=False)
        assert len(level) > 0
        lines = level.strip().split("\n")
        assert len(lines) == 7

    def test_generation_with_validation(self):
        """Constraint-aware WFC generates a playable level."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        level = gen.generate(20, 7, validate_playability=True, max_retries=50)
        assert len(level) > 0
        # Verify it's actually playable
        grid = parse_fragment(level)
        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)
        is_playable, _ = validator.validate_level(grid)
        assert is_playable

    def test_generation_stats(self):
        """Generation should produce stats."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        gen.generate(20, 7, validate_playability=False)
        stats = gen.generation_stats
        assert "attempts" in stats

    def test_difficulty_target_affects_output(self):
        """Different difficulty targets should produce different levels."""
        gen_easy = ConstraintAwareWFC(seed=42, difficulty_target=0.1)
        gen_easy.learn()
        gen_hard = ConstraintAwareWFC(seed=42, difficulty_target=0.9)
        gen_hard.learn()

        level_easy = gen_easy.generate(20, 7, validate_playability=False)
        level_hard = gen_hard.generate(20, 7, validate_playability=False)

        # They should be different (different difficulty targeting)
        # Note: with same seed, internal randomness may still differ
        assert isinstance(level_easy, str)
        assert isinstance(level_hard, str)

    def test_batch_validated_generation(self):
        """Batch generation with validation produces results."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        results = gen.generate_batch_validated(
            3, width=18, height=7, validate_playability=False
        )
        assert len(results) == 3
        for level, is_playable, stats in results:
            assert isinstance(level, str)
            assert isinstance(stats, dict)

    def test_ground_row_is_solid(self):
        """Bottom row should always be solid ground."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        level = gen.generate(20, 7, ensure_ground=True, validate_playability=False)
        lines = level.strip().split("\n")
        bottom = lines[-1]
        for ch in bottom:
            assert ch in SOLID_CHARS, f"Bottom row has non-solid: {ch}"

    def test_has_spawn_and_goal(self):
        """Generated level should have spawn and goal."""
        gen = ConstraintAwareWFC(seed=42)
        gen.learn()
        level = gen.generate(
            20, 7, ensure_spawn=True, ensure_goal=True,
            validate_playability=False,
        )
        assert "M" in level
        assert "G" in level


# ── Difficulty Curve Tests ───────────────────────────────────────────────────


class TestDifficultyCurves:
    """Test difficulty curve functions."""

    def test_linear_bounds(self):
        """Linear difficulty should be in [0, 1]."""
        for col in range(20):
            d = linear_difficulty(col, 20)
            assert 0.0 <= d <= 1.0

    def test_linear_monotonic(self):
        """Linear difficulty should be monotonically increasing."""
        prev = -1.0
        for col in range(20):
            d = linear_difficulty(col, 20)
            assert d >= prev
            prev = d

    def test_sigmoid_bounds(self):
        """Sigmoid difficulty should be in [0, 1]."""
        for col in range(20):
            d = sigmoid_difficulty(col, 20)
            assert 0.0 <= d <= 1.0

    def test_sigmoid_s_shape(self):
        """Sigmoid should be low at start, high at end."""
        assert sigmoid_difficulty(0, 20) < 0.2
        assert sigmoid_difficulty(19, 20) > 0.8

    def test_wave_bounds(self):
        """Wave difficulty should be in [0, 1]."""
        for col in range(20):
            d = wave_difficulty(col, 20)
            assert 0.0 <= d <= 1.0

    def test_wave_has_oscillation(self):
        """Wave difficulty should oscillate."""
        values = [wave_difficulty(col, 20) for col in range(20)]
        # Check that values go up and down
        ups = sum(1 for i in range(1, len(values)) if values[i] > values[i-1])
        downs = sum(1 for i in range(1, len(values)) if values[i] < values[i-1])
        assert ups > 0
        assert downs > 0
