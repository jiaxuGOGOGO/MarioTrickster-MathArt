"""L-System procedural plant generator for 2D pixel art.

This module implements a parametric Lindenmayer System (L-System) that generates
plant structures (trees, bushes, grass, vines) as collections of SDF primitives.
The generated plants can be rendered through the existing SDF renderer and
automatically styled with OKLAB palettes.

Grammar symbols:
  F  — Draw forward (trunk/branch segment)
  f  — Move forward without drawing
  +  — Turn right by angle
  -  — Turn left by angle
  [  — Push state (start branch)
  ]  — Pop state (end branch)
  L  — Draw leaf
  *  — Draw flower/fruit

Usage::

    from mathart.sdf.lsystem import LSystem, PlantPresets
    plant = PlantPresets.oak_tree()
    segments = plant.generate(iterations=4)
    img = plant.render(32, 32)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class LSystemRule:
    """A production rule for the L-System grammar."""

    predecessor: str
    successor: str
    probability: float = 1.0  # For stochastic L-systems


@dataclass
class Segment:
    """A rendered segment of the plant structure."""

    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    segment_type: str  # "trunk", "branch", "leaf", "flower"
    depth: int  # Recursion depth (0 = root)


@dataclass
class TurtleState:
    """Turtle graphics state for interpreting L-System strings."""

    x: float = 0.0
    y: float = 0.0
    angle: float = -90.0  # Start pointing up (degrees)
    width: float = 3.0
    depth: int = 0


class LSystem:
    """Parametric L-System plant generator.

    Parameters
    ----------
    axiom : str
        Initial string (seed).
    rules : list[LSystemRule]
        Production rules.
    angle : float
        Default turning angle in degrees.
    length : float
        Base segment length.
    width : float
        Base trunk width.
    length_decay : float
        Length multiplier per depth level (< 1.0 for tapering).
    width_decay : float
        Width multiplier per depth level.
    seed : int or None
        Random seed for stochastic rules.
    """

    def __init__(
        self,
        axiom: str = "F",
        rules: Optional[list[LSystemRule]] = None,
        angle: float = 25.0,
        length: float = 5.0,
        width: float = 3.0,
        length_decay: float = 0.75,
        width_decay: float = 0.7,
        seed: Optional[int] = None,
    ):
        self.axiom = axiom
        self.rules = rules or []
        self.angle = angle
        self.length = length
        self.width = width
        self.length_decay = length_decay
        self.width_decay = width_decay
        self.rng = random.Random(seed)
        self._segments: list[Segment] = []

    def iterate(self, iterations: int = 3) -> str:
        """Apply production rules iteratively to generate the L-System string."""
        current = self.axiom
        for _ in range(iterations):
            next_str = []
            for char in current:
                replaced = False
                # Collect matching rules
                matching = [r for r in self.rules if r.predecessor == char]
                if matching:
                    # Stochastic selection
                    total_prob = sum(r.probability for r in matching)
                    roll = self.rng.random() * total_prob
                    cumulative = 0.0
                    for rule in matching:
                        cumulative += rule.probability
                        if roll <= cumulative:
                            next_str.append(rule.successor)
                            replaced = True
                            break
                if not replaced:
                    next_str.append(char)
            current = "".join(next_str)
        return current

    def interpret(self, lstring: str) -> list[Segment]:
        """Interpret an L-System string using turtle graphics.

        Returns a list of Segment objects representing the plant structure.
        """
        state = TurtleState(x=0.0, y=0.0, angle=-90.0, width=self.width, depth=0)
        stack: list[TurtleState] = []
        segments: list[Segment] = []
        current_length = self.length

        for char in lstring:
            if char == "F":
                # Draw forward
                rad = math.radians(state.angle)
                seg_len = current_length * (self.length_decay ** state.depth)
                nx = state.x + seg_len * math.cos(rad)
                ny = state.y + seg_len * math.sin(rad)
                seg_width = max(0.5, state.width * (self.width_decay ** state.depth))
                seg_type = "trunk" if state.depth == 0 else "branch"
                segments.append(
                    Segment(state.x, state.y, nx, ny, seg_width, seg_type, state.depth)
                )
                state.x = nx
                state.y = ny
            elif char == "f":
                # Move forward without drawing
                rad = math.radians(state.angle)
                seg_len = current_length * (self.length_decay ** state.depth)
                state.x += seg_len * math.cos(rad)
                state.y += seg_len * math.sin(rad)
            elif char == "+":
                state.angle += self.angle
            elif char == "-":
                state.angle -= self.angle
            elif char == "[":
                stack.append(
                    TurtleState(
                        x=state.x,
                        y=state.y,
                        angle=state.angle,
                        width=state.width,
                        depth=state.depth,
                    )
                )
                state.depth += 1
            elif char == "]":
                if stack:
                    state = stack.pop()
            elif char == "L":
                # Leaf: small circle at current position
                segments.append(
                    Segment(
                        state.x, state.y,
                        state.x, state.y,
                        max(1.0, state.width * 0.8),
                        "leaf", state.depth,
                    )
                )
            elif char == "*":
                # Flower/fruit
                segments.append(
                    Segment(
                        state.x, state.y,
                        state.x, state.y,
                        max(1.5, state.width * 1.2),
                        "flower", state.depth,
                    )
                )

        self._segments = segments
        return segments

    def generate(self, iterations: int = 3) -> list[Segment]:
        """Generate plant structure: iterate rules then interpret."""
        lstring = self.iterate(iterations)
        return self.interpret(lstring)

    def render(
        self,
        width: int = 32,
        height: int = 32,
        palette: Optional[list[tuple[int, int, int, int]]] = None,
        background: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> Image.Image:
        """Render the generated plant to a pixel art image.

        Parameters
        ----------
        width, height : int
            Output image dimensions in pixels.
        palette : list of RGBA tuples or None
            Colors for [trunk, branch, leaf, flower]. If None, uses defaults.
        background : RGBA tuple
            Background color.

        Returns
        -------
        PIL.Image.Image
            RGBA image of the rendered plant.
        """
        if not self._segments:
            raise RuntimeError("No segments generated. Call generate() first.")

        if palette is None:
            palette = [
                (101, 67, 33, 255),   # trunk: brown
                (139, 90, 43, 255),   # branch: lighter brown
                (34, 139, 34, 255),   # leaf: green
                (255, 105, 180, 255), # flower: pink
            ]

        # Compute bounding box of all segments
        all_x = []
        all_y = []
        for seg in self._segments:
            all_x.extend([seg.x0, seg.x1])
            all_y.extend([seg.y0, seg.y1])

        if not all_x:
            return Image.new("RGBA", (width, height), background)

        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)

        # Add padding
        range_x = max(max_x - min_x, 1)
        range_y = max(max_y - min_y, 1)
        padding = 0.1
        min_x -= range_x * padding
        max_x += range_x * padding
        min_y -= range_y * padding
        max_y += range_y * padding
        range_x = max_x - min_x
        range_y = max_y - min_y

        # Create image
        img = Image.new("RGBA", (width, height), background)
        pixels = img.load()

        # Map segment types to palette indices
        type_to_idx = {"trunk": 0, "branch": 1, "leaf": 2, "flower": 3}

        # Render segments (back to front: deeper segments first)
        sorted_segs = sorted(self._segments, key=lambda s: -s.depth)

        for seg in sorted_segs:
            color_idx = type_to_idx.get(seg.segment_type, 0)
            color = palette[min(color_idx, len(palette) - 1)]

            if seg.segment_type in ("leaf", "flower"):
                # Render as filled circle
                cx = int((seg.x0 - min_x) / range_x * (width - 1))
                cy = int((seg.y0 - min_y) / range_y * (height - 1))
                radius = max(1, int(seg.width * width / range_x * 0.3))
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        if dx * dx + dy * dy <= radius * radius:
                            px, py = cx + dx, cy + dy
                            if 0 <= px < width and 0 <= py < height:
                                pixels[px, py] = color
            else:
                # Render as line (Bresenham-like)
                x0 = int((seg.x0 - min_x) / range_x * (width - 1))
                y0 = int((seg.y0 - min_y) / range_y * (height - 1))
                x1 = int((seg.x1 - min_x) / range_x * (width - 1))
                y1 = int((seg.y1 - min_y) / range_y * (height - 1))
                line_width = max(1, int(seg.width * width / range_x * 0.15))
                self._draw_thick_line(pixels, x0, y0, x1, y1, line_width, color, width, height)

        return img

    @staticmethod
    def _draw_thick_line(
        pixels, x0, y0, x1, y1, thickness, color, img_w, img_h
    ) -> None:
        """Draw a thick line using Bresenham's algorithm with thickness."""
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        half_t = thickness // 2

        while True:
            for tx in range(-half_t, half_t + 1):
                for ty in range(-half_t, half_t + 1):
                    px, py = x0 + tx, y0 + ty
                    if 0 <= px < img_w and 0 <= py < img_h:
                        pixels[px, py] = color
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy


class PlantPresets:
    """Pre-configured L-System plant presets for common game vegetation."""

    @staticmethod
    def oak_tree(seed: Optional[int] = None) -> LSystem:
        """A broad oak-like tree with spreading branches."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "FF+[+F-F-FL]-[-F+F+FL]", probability=0.7),
                LSystemRule("F", "FF-[-F+FL]+[+F-FL]", probability=0.3),
            ],
            angle=25.0,
            length=6.0,
            width=3.0,
            length_decay=0.72,
            width_decay=0.65,
            seed=seed,
        )

    @staticmethod
    def pine_tree(seed: Optional[int] = None) -> LSystem:
        """A tall, narrow pine/conifer tree."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "FF[+F-FL][-F+FL]", probability=0.8),
                LSystemRule("F", "FF[+FL][-FL]", probability=0.2),
            ],
            angle=35.0,
            length=5.0,
            width=2.5,
            length_decay=0.68,
            width_decay=0.6,
            seed=seed,
        )

    @staticmethod
    def bush(seed: Optional[int] = None) -> LSystem:
        """A low, dense bush."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[+F][-F]FL", probability=0.6),
                LSystemRule("F", "F[+FL][-FL]", probability=0.4),
            ],
            angle=30.0,
            length=3.0,
            width=2.0,
            length_decay=0.8,
            width_decay=0.75,
            seed=seed,
        )

    @staticmethod
    def grass(seed: Optional[int] = None) -> LSystem:
        """Simple grass blades."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[+F]F[-F]F", probability=0.5),
                LSystemRule("F", "F[+F][-F]", probability=0.5),
            ],
            angle=15.0,
            length=4.0,
            width=1.0,
            length_decay=0.85,
            width_decay=0.9,
            seed=seed,
        )

    @staticmethod
    def vine(seed: Optional[int] = None) -> LSystem:
        """A hanging vine with leaves."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[-FL][+FL]F", probability=0.6),
                LSystemRule("F", "FF[-FL]+FL", probability=0.4),
            ],
            angle=20.0,
            length=4.0,
            width=1.5,
            length_decay=0.78,
            width_decay=0.7,
            seed=seed,
        )

    @staticmethod
    def flower_plant(seed: Optional[int] = None) -> LSystem:
        """A flowering plant with blossoms."""
        return LSystem(
            axiom="F",
            rules=[
                LSystemRule("F", "F[+F*][-F*]FL", probability=0.5),
                LSystemRule("F", "FF[+F*L][-FL]", probability=0.5),
            ],
            angle=30.0,
            length=4.0,
            width=2.0,
            length_decay=0.75,
            width_decay=0.65,
            seed=seed,
        )

    @classmethod
    def all_presets(cls) -> dict[str, LSystem]:
        """Return all available plant presets."""
        return {
            "oak_tree": cls.oak_tree(),
            "pine_tree": cls.pine_tree(),
            "bush": cls.bush(),
            "grass": cls.grass(),
            "vine": cls.vine(),
            "flower_plant": cls.flower_plant(),
        }
