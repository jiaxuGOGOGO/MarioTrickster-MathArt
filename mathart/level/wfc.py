"""Wave Function Collapse (WFC) level generator for MarioTrickster.

This module implements a tile-based WFC algorithm that learns adjacency rules
from existing ASCII level fragments and generates new, structurally valid
level layouts. The output is an ASCII string directly compatible with the
main project's Level Studio.

Algorithm overview:
  1. **Learn**: Scan training fragments to build adjacency probability tables.
  2. **Observe**: Find the cell with lowest entropy (fewest remaining options).
  3. **Collapse**: Randomly select a tile for that cell, weighted by frequency.
  4. **Propagate**: Remove incompatible options from neighbouring cells.
  5. Repeat 2-4 until all cells are resolved or a contradiction is detected.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from .templates import (
    CLASSIC_FRAGMENTS,
    ELEMENT_MAP,
    SOLID_CHARS,
    PLATFORM_CHARS,
    HAZARD_CHARS,
    ENEMY_CHARS,
    SPAWN_CHARS,
    AIR_CHAR,
    parse_fragment,
    fragment_to_string,
)


# Direction vectors: up, right, down, left
DIRECTIONS = [(-1, 0), (0, 1), (1, 0), (0, -1)]
DIR_NAMES = ["up", "right", "down", "left"]
OPPOSITE = {0: 2, 1: 3, 2: 0, 3: 1}


@dataclass
class AdjacencyRules:
    """Stores learned adjacency probabilities between tile types.

    For each direction (up/right/down/left), maintains a mapping from
    a source tile to a Counter of valid neighbour tiles and their frequencies.
    """

    rules: dict[int, dict[str, Counter]] = field(default_factory=dict)
    tile_weights: Counter = field(default_factory=Counter)

    def __post_init__(self):
        for d in range(4):
            if d not in self.rules:
                self.rules[d] = defaultdict(Counter)

    def learn_from_grid(self, grid: list[list[str]]) -> None:
        """Extract adjacency rules from a single 2D grid."""
        rows = len(grid)
        for r in range(rows):
            cols = len(grid[r])
            for c in range(cols):
                tile = grid[r][c]
                self.tile_weights[tile] += 1
                for d, (dr, dc) in enumerate(DIRECTIONS):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < len(grid[nr]):
                        neighbour = grid[nr][nc]
                        self.rules[d][tile][neighbour] += 1

    def learn_from_fragments(
        self, fragments: Optional[dict[str, str]] = None
    ) -> None:
        """Learn adjacency rules from a dictionary of named fragments."""
        if fragments is None:
            fragments = CLASSIC_FRAGMENTS
        for name, text in fragments.items():
            grid = parse_fragment(text)
            self.learn_from_grid(grid)

    def get_allowed(self, tile: str, direction: int) -> set[str]:
        """Return the set of tiles allowed adjacent to *tile* in *direction*."""
        if tile in self.rules[direction]:
            return set(self.rules[direction][tile].keys())
        return set()

    def get_weights(self, tile: str, direction: int) -> Counter:
        """Return weighted counts of allowed neighbours."""
        return self.rules[direction].get(tile, Counter())

    @property
    def all_tiles(self) -> set[str]:
        """Return the set of all known tile types."""
        return set(self.tile_weights.keys())


@dataclass
class _Cell:
    """Represents a single cell in the WFC grid."""

    options: set[str]
    collapsed: bool = False
    tile: Optional[str] = None
    is_locked: bool = False

    @property
    def entropy(self) -> float:
        if self.collapsed or self.is_locked:
            return 0.0
        n = len(self.options)
        if n <= 1:
            return 0.0
        return math.log2(n)


class WFCGenerator:
    """Wave Function Collapse generator for ASCII level layouts.

    Usage::

        gen = WFCGenerator(seed=42)
        gen.learn()                          # learn from classic fragments
        ascii_level = gen.generate(20, 7)    # 20 cols × 7 rows
        print(ascii_level)
    """

    def __init__(self, seed: Optional[int] = None):
        self.rules = AdjacencyRules()
        self.rng = random.Random(seed)
        self._grid: list[list[_Cell]] = []
        self._rows = 0
        self._cols = 0

    # ── Public API ────────────────────────────────────────────────────

    def learn(
        self, fragments: Optional[dict[str, str]] = None
    ) -> "WFCGenerator":
        """Learn adjacency rules from level fragments. Returns self for chaining."""
        self.rules.learn_from_fragments(fragments)
        return self

    def generate(
        self,
        width: int = 22,
        height: int = 7,
        *,
        ensure_ground: bool = True,
        ensure_spawn: bool = True,
        ensure_goal: bool = True,
        max_retries: int = 20,
    ) -> str:
        """Generate a new level layout as an ASCII string.

        Parameters
        ----------
        width : int
            Number of columns.
        height : int
            Number of rows.
        ensure_ground : bool
            Force the bottom row to be solid ground.
        ensure_spawn : bool
            Ensure at least one Mario spawn point exists.
        ensure_goal : bool
            Ensure at least one goal zone exists.
        max_retries : int
            Maximum number of restart attempts on contradiction.

        Returns
        -------
        str
            ASCII level string compatible with Level Studio.
        """
        if not self.rules.all_tiles:
            raise RuntimeError("No adjacency rules learned. Call learn() first.")

        for attempt in range(max_retries):
            try:
                result = self._try_generate(
                    width, height, ensure_ground, ensure_spawn, ensure_goal
                )
                return result
            except _Contradiction:
                continue

        raise RuntimeError(
            f"WFC failed to generate a valid level after {max_retries} retries."
        )

    def generate_batch(
        self, count: int, width: int = 22, height: int = 7, **kwargs
    ) -> list[str]:
        """Generate multiple unique level layouts."""
        levels = []
        for _ in range(count):
            levels.append(self.generate(width, height, **kwargs))
        return levels

    # ── Internal algorithm ────────────────────────────────────────────

    def _try_generate(
        self,
        width: int,
        height: int,
        ensure_ground: bool,
        ensure_spawn: bool,
        ensure_goal: bool,
    ) -> str:
        self._rows = height
        self._cols = width
        all_tiles = self.rules.all_tiles

        # Initialize grid with all options
        self._grid = [
            [_Cell(options=set(all_tiles)) for _ in range(width)]
            for _ in range(height)
        ]

        # Apply structural constraints
        if ensure_ground:
            self._force_bottom_ground()
        if ensure_spawn:
            self._force_top_air()

        self._propagate_locked_cells()

        # Main WFC loop
        while True:
            cell_pos = self._find_min_entropy()
            if cell_pos is None:
                break  # All cells collapsed
            r, c = cell_pos
            self._collapse(r, c)
            self._propagate(r, c)

        # Post-processing: ensure spawn and goal
        result_grid = [
            [cell.tile or AIR_CHAR for cell in row] for row in self._grid
        ]

        if ensure_spawn:
            self._place_element(result_grid, "M", row_preference="upper")
        if ensure_goal:
            self._place_element(result_grid, "G", row_preference="upper", col_preference="right")

        return fragment_to_string(result_grid)

    def _force_bottom_ground(self) -> None:
        """Force the bottom row to be solid ground."""
        bottom = self._rows - 1
        for c in range(self._cols):
            self._lock_cell(bottom, c, "#")

    def _force_top_air(self) -> None:
        """Force the top two rows to be mostly air (for playability)."""
        for r in range(min(2, self._rows)):
            for c in range(self._cols):
                cell = self._grid[r][c]
                # Keep air and collectibles, remove heavy elements
                air_options = cell.options - SOLID_CHARS - HAZARD_CHARS
                if air_options:
                    cell.options = air_options

    def _lock_cell(self, r: int, c: int, tile: str) -> None:
        """Collapse a cell to a read-only singleton domain."""
        cell = self._grid[r][c]
        cell.options = {tile}
        cell.tile = tile
        cell.collapsed = True
        cell.is_locked = True

    def _propagate_locked_cells(self) -> None:
        """Seed propagation from every locked cell so hard constraints radiate."""
        locked_seeds = [
            (r, c)
            for r in range(self._rows)
            for c in range(self._cols)
            if self._grid[r][c].is_locked
        ]
        if locked_seeds:
            self._propagate_queue(locked_seeds)

    def _find_min_entropy(self) -> Optional[tuple[int, int]]:
        """Find the uncollapsed cell with the lowest entropy."""
        min_entropy = float("inf")
        candidates = []
        for r in range(self._rows):
            for c in range(self._cols):
                cell = self._grid[r][c]
                if cell.collapsed:
                    continue
                if len(cell.options) == 0:
                    raise _Contradiction(f"Cell ({r},{c}) has no options")
                e = cell.entropy + self.rng.random() * 0.001  # tie-breaking
                if e < min_entropy:
                    min_entropy = e
                    candidates = [(r, c)]
                elif abs(e - min_entropy) < 0.01:
                    candidates.append((r, c))
        if not candidates:
            return None
        return self.rng.choice(candidates)

    def _collapse(self, r: int, c: int) -> None:
        """Collapse a cell to a single tile, weighted by global frequency."""
        cell = self._grid[r][c]
        options = list(cell.options)
        weights = [max(self.rules.tile_weights.get(t, 1), 1) for t in options]
        total = sum(weights)
        probs = [w / total for w in weights]
        chosen = self.rng.choices(options, weights=probs, k=1)[0]
        cell.tile = chosen
        cell.collapsed = True
        cell.options = {chosen}

    def _propagate(self, start_r: int, start_c: int) -> None:
        """Propagate constraints from a collapsed cell outward."""
        self._propagate_queue([(start_r, start_c)])

    def _propagate_queue(self, seeds: list[tuple[int, int]]) -> None:
        """Run propagation from one or more seed cells using a queue."""
        queue = deque(seeds)
        while queue:
            r, c = queue.popleft()
            cell = self._grid[r][c]
            if len(cell.options) == 0:
                raise _Contradiction(f"Cell ({r},{c}) has no options")

            for d, (dr, dc) in enumerate(DIRECTIONS):
                nr, nc = r + dr, c + dc
                if not (0 <= nr < self._rows and 0 <= nc < self._cols):
                    continue

                allowed = set()
                for tile in cell.options:
                    allowed |= self.rules.get_allowed(tile, d)

                if self._restrict_cell_options(
                    nr, nc, allowed, source_r=r, source_c=c, direction=d
                ):
                    queue.append((nr, nc))

    def _restrict_cell_options(
        self,
        target_r: int,
        target_c: int,
        allowed: set[str],
        *,
        source_r: int,
        source_c: int,
        direction: int,
    ) -> bool:
        """Intersect a target cell domain with allowed options.

        Locked cells are treated as read-only singleton domains. Any attempted
        reduction of a locked domain is raised as an explicit conflict so the
        caller can restart or backtrack without silently overwriting ground
        truth.
        """
        target = self._grid[target_r][target_c]
        new_options = target.options & allowed
        if new_options == target.options:
            return False

        if target.is_locked:
            raise WFCConstraintConflictError(
                "Locked cell conflict at "
                f"({target_r},{target_c}) while propagating "
                f"{DIR_NAMES[direction]} from ({source_r},{source_c}); "
                f"locked domain {sorted(target.options)} would shrink to "
                f"{sorted(new_options)}"
            )

        if len(new_options) == 0:
            raise _Contradiction(
                f"Propagation emptied cell ({target_r},{target_c})"
            )

        target.options = new_options
        if len(new_options) == 1:
            target.tile = next(iter(new_options))
            target.collapsed = True
        else:
            target.tile = None
            target.collapsed = False
        return True

    def _place_element(
        self,
        grid: list[list[str]],
        char: str,
        row_preference: str = "upper",
        col_preference: str = "left",
    ) -> None:
        """Place a special element (spawn/goal) on a valid position.

        Valid position: air cell with solid ground directly below.
        """
        candidates = []
        for r in range(self._rows - 1):
            for c in range(self._cols):
                if grid[r][c] == AIR_CHAR:
                    below = grid[r + 1][c] if r + 1 < self._rows else None
                    if below in SOLID_CHARS or below in PLATFORM_CHARS:
                        candidates.append((r, c))

        if not candidates:
            # Fallback: place anywhere that's air
            for r in range(self._rows):
                for c in range(self._cols):
                    if grid[r][c] == AIR_CHAR:
                        candidates.append((r, c))

        if not candidates:
            return  # Cannot place

        # Sort by preference
        def sort_key(pos):
            r, c = pos
            row_score = r if row_preference == "upper" else (self._rows - r)
            col_score = c if col_preference == "left" else (self._cols - c)
            return (row_score, col_score)

        candidates.sort(key=sort_key)
        # Pick from top candidates with some randomness
        pick_range = min(5, len(candidates))
        r, c = self.rng.choice(candidates[:pick_range])
        grid[r][c] = char


class _Contradiction(Exception):
    """Raised when WFC reaches an impossible state."""
    pass


class WFCConstraintConflictError(_Contradiction):
    """Raised when propagation attempts to overwrite a locked cell domain."""
    pass
