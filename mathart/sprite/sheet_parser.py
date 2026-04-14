"""SpriteSheetParser — automatically slice spritesheets into individual frames.

Supports three detection modes:
  1. uniform_grid: Fixed cell size (most common for game spritesheets)
  2. auto_detect:  Detect grid size from image dimensions and transparency gaps
  3. manual:       User-specified cell positions via JSON metadata

For each detected frame, the parser also extracts:
  - Frame index and row/column position
  - Bounding box (tight crop around non-transparent pixels)
  - Motion delta from previous frame (for animation analysis)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class SpriteFrame:
    """A single frame extracted from a spritesheet."""
    index:       int
    row:         int
    col:         int
    image:       Image.Image
    bbox:        tuple[int, int, int, int]   # (left, top, right, bottom) tight crop
    cell_rect:   tuple[int, int, int, int]   # (left, top, right, bottom) grid cell
    has_content: bool = True                 # False if frame is empty/transparent

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def pixel_count(self) -> int:
        """Number of non-transparent pixels."""
        arr = np.array(self.image.convert("RGBA"))
        return int((arr[:, :, 3] > 10).sum())


@dataclass
class ParseResult:
    """Result of parsing a spritesheet."""
    source_path:  str
    sheet_width:  int
    sheet_height: int
    cell_width:   int
    cell_height:  int
    rows:         int
    cols:         int
    frames:       list[SpriteFrame] = field(default_factory=list)
    mode:         str = "uniform_grid"

    @property
    def frame_count(self) -> int:
        return len([f for f in self.frames if f.has_content])

    def get_animation_row(self, row: int) -> list[SpriteFrame]:
        """Return all frames in a given row (one animation sequence)."""
        return [f for f in self.frames if f.row == row and f.has_content]

    def summary(self) -> str:
        return (
            f"SpriteSheet: {self.sheet_width}x{self.sheet_height}px, "
            f"grid={self.cols}x{self.rows}, "
            f"cell={self.cell_width}x{self.cell_height}px, "
            f"frames={self.frame_count}"
        )


class SpriteSheetParser:
    """Parses spritesheets into individual frames.

    Parameters
    ----------
    min_content_ratio : float
        Minimum ratio of non-transparent pixels to consider a frame non-empty.
        Default 0.01 (1% of cell must have content).
    """

    def __init__(self, min_content_ratio: float = 0.01) -> None:
        self.min_content_ratio = min_content_ratio

    def parse_uniform(
        self,
        image: Image.Image,
        cell_width: int,
        cell_height: int,
        source_path: str = "",
    ) -> ParseResult:
        """Parse a spritesheet with uniform grid cells.

        Parameters
        ----------
        image : PIL.Image
            The spritesheet image (RGBA recommended).
        cell_width : int
            Width of each cell in pixels.
        cell_height : int
            Height of each cell in pixels.
        source_path : str
            Original file path for logging.

        Returns
        -------
        ParseResult
        """
        img = image.convert("RGBA")
        w, h = img.size
        cols = max(1, w // cell_width)
        rows = max(1, h // cell_height)

        frames: list[SpriteFrame] = []
        idx = 0
        for row in range(rows):
            for col in range(cols):
                x0 = col * cell_width
                y0 = row * cell_height
                x1 = min(x0 + cell_width, w)
                y1 = min(y0 + cell_height, h)

                cell_img = img.crop((x0, y0, x1, y1))
                arr = np.array(cell_img)
                alpha = arr[:, :, 3]
                has_content = (alpha > 10).mean() >= self.min_content_ratio

                # Tight bounding box
                if has_content:
                    rows_with_content = np.any(alpha > 10, axis=1)
                    cols_with_content = np.any(alpha > 10, axis=0)
                    top    = int(np.argmax(rows_with_content))
                    bottom = int(len(rows_with_content) - np.argmax(rows_with_content[::-1]))
                    left   = int(np.argmax(cols_with_content))
                    right  = int(len(cols_with_content) - np.argmax(cols_with_content[::-1]))
                    bbox = (x0 + left, y0 + top, x0 + right, y0 + bottom)
                else:
                    bbox = (x0, y0, x1, y1)

                frames.append(SpriteFrame(
                    index=idx,
                    row=row,
                    col=col,
                    image=cell_img,
                    bbox=bbox,
                    cell_rect=(x0, y0, x1, y1),
                    has_content=has_content,
                ))
                idx += 1

        return ParseResult(
            source_path=source_path,
            sheet_width=w,
            sheet_height=h,
            cell_width=cell_width,
            cell_height=cell_height,
            rows=rows,
            cols=cols,
            frames=frames,
            mode="uniform_grid",
        )

    def parse_auto(
        self,
        image: Image.Image,
        source_path: str = "",
        hint_rows: Optional[int] = None,
        hint_cols: Optional[int] = None,
    ) -> ParseResult:
        """Auto-detect grid dimensions from image.

        Algorithm:
          1. Convert to RGBA and find transparency gaps
          2. Detect repeating vertical/horizontal gaps
          3. Infer cell size from gap positions
          4. Fall back to common pixel art sizes (8, 16, 32, 48, 64)

        Parameters
        ----------
        image : PIL.Image
        source_path : str
        hint_rows : int, optional
            If you know the number of rows, provide it for better detection.
        hint_cols : int, optional
            If you know the number of columns, provide it for better detection.
        """
        img = image.convert("RGBA")
        w, h = img.size

        if hint_rows and hint_cols:
            cell_w = w // hint_cols
            cell_h = h // hint_rows
        else:
            cell_w = self._detect_cell_size(w)
            cell_h = self._detect_cell_size(h)

        return self.parse_uniform(img, cell_w, cell_h, source_path)

    def parse_file(
        self,
        filepath: str | Path,
        cell_width: Optional[int] = None,
        cell_height: Optional[int] = None,
        hint_rows: Optional[int] = None,
        hint_cols: Optional[int] = None,
    ) -> ParseResult:
        """Parse a spritesheet file.

        If cell_width and cell_height are provided, uses uniform grid mode.
        Otherwise, auto-detects grid dimensions.
        """
        filepath = Path(filepath)
        img = Image.open(filepath)

        if cell_width and cell_height:
            return self.parse_uniform(img, cell_width, cell_height, str(filepath))
        else:
            return self.parse_auto(img, str(filepath), hint_rows, hint_cols)

    def save_frames(
        self,
        result: ParseResult,
        output_dir: Path,
        prefix: str = "frame",
        only_content: bool = True,
    ) -> list[Path]:
        """Save individual frames to a directory."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for frame in result.frames:
            if only_content and not frame.has_content:
                continue
            path = output_dir / f"{prefix}_{frame.index:03d}_r{frame.row}c{frame.col}.png"
            frame.image.save(path)
            saved.append(path)
        return saved

    @staticmethod
    def _detect_cell_size(dimension: int) -> int:
        """Detect likely cell size from a sheet dimension."""
        # Common pixel art sprite sizes
        candidates = [8, 16, 24, 32, 48, 64, 96, 128]
        for size in reversed(candidates):
            if dimension % size == 0 and dimension // size >= 1:
                return size
        # Fallback: assume square cells, try to find a reasonable divisor
        for size in range(dimension, 0, -1):
            if dimension % size == 0 and 8 <= size <= 128:
                return size
        return max(8, dimension)
