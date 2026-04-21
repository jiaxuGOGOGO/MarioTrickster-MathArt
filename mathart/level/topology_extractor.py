"""Tensor-based level topology extractor for P1-ARCH-6.

SESSION-109 — Pure NumPy / SciPy implementation that consumes a logical
tile-id grid and produces the strongly-typed
:class:`mathart.level.topology_types.TopologyExtractionResult`.

Algorithmic recipe
------------------
1. **Physical attribute tensor**: convert the discrete tile-id grid into
   boolean masks (``is_solid``, ``is_empty``) using the *physical
   attribute table* — never via ``if tile == "GRASS"`` style hardcoding.
   The table is provided by the caller or defaults to the project's
   ``mathart.level.wfc_tilemap_exporter.DualGridMapper.SOLID_IDS`` set.
2. **Convolution-based feature detection**: every adjacency / corner /
   slope feature is detected by a 3x3 convolution kernel running over the
   ``is_solid`` mask, exactly the way Recast voxel contours and Townscaper
   dual grids extract their geometry primitives.  Python-level row/col
   loops are forbidden — the extractor uses
   :func:`scipy.signal.convolve2d` and :mod:`numpy` boolean broadcasting.
3. **Surface normal field**: the gradient of the binary solid mask gives
   an outward-pointing normal vector at every collision boundary cell,
   computed via a single ``np.gradient`` call (vectorised, O(N)).
4. **Connected components**: walkable-surface cells are labelled with
   :func:`scipy.ndimage.label`, producing the
   :class:`TraversalLane` graph nodes used by AI navigation.

Performance discipline
----------------------
* Target: 512x512 grid in ``< 50 ms`` on the maintainer's i5-12600KF.
* Memory: one ``np.uint8`` plus a few ``bool`` masks; never a
  ``list[list[...]]`` of pure Python objects.
* Threading: SciPy / NumPy delegate to OpenBLAS; the extractor itself
  remains GIL-free for embarrassingly parallel callers.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable, Optional, Sequence, Set

import numpy as np
from scipy import ndimage
from scipy.signal import convolve2d

from .topology_types import (
    ANCHOR_CEILING,
    ANCHOR_CORNER_CONCAVE,
    ANCHOR_CORNER_CONVEX,
    ANCHOR_FLOOR_TOP,
    ANCHOR_SLOPE,
    ANCHOR_WALL_LEFT,
    ANCHOR_WALL_RIGHT,
    SURFACE_FLOOR,
    SURFACE_PLATFORM,
    SURFACE_SLOPE,
    SemanticAnchor,
    TopologyExtractionResult,
    TopologyTensors,
    TraversalLane,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default physical attribute table — DOD discipline, no hardcoded chars
# ---------------------------------------------------------------------------

#: Default solid tile-ids.  Mirrors :class:`DualGridMapper.SOLID_IDS` but is
#: deliberately re-declared here so the extractor has a stable default
#: even when the WFC tilemap exporter module is not importable.
_DEFAULT_SOLID_IDS: frozenset[int] = frozenset({1, 12, 13, 2, 8, 9, 10, 11})

#: Tile-ids treated as one-way / platform surfaces (walkable from above
#: only).  These contribute to ``is_walkable_surface`` but not to
#: ``is_collision_boundary`` for vertical neighbours below.
_DEFAULT_PLATFORM_IDS: frozenset[int] = frozenset({2, 11})


# ---------------------------------------------------------------------------
# Internal kernel cache (compiled once at import time)
# ---------------------------------------------------------------------------

#: Center-only kernel — used as the identity for convolution-based
#: neighbourhood lookups.
_K_CENTER = np.array([[0, 0, 0], [0, 1, 0], [0, 0, 0]], dtype=np.int8)

#: Cross-shaped 4-neighbour kernel.
_K_CROSS = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=np.int8)

#: Anti-diagonal kernels for slope detection.  ``_K_SLOPE_NE`` matches a
#: stair-stepping diagonal solid pattern rising to the north-east.
_K_SLOPE_NE = np.array(
    [
        [0, 0, 1],
        [0, 1, 0],
        [1, 0, 0],
    ],
    dtype=np.int8,
)
_K_SLOPE_NW = np.array(
    [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    ],
    dtype=np.int8,
)


def _convolve_uint8(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolve ``mask`` (bool / uint8) with ``kernel`` returning ``int16``.

    Wraps :func:`scipy.signal.convolve2d` with a ``"same"`` boundary so the
    output retains the input shape, and uses ``"fill"`` with ``fillvalue=0``
    so out-of-grid cells are treated as empty (matches Recast voxel
    contour boundary semantics).
    """
    return convolve2d(
        mask.astype(np.uint8),
        kernel.astype(np.uint8),
        mode="same",
        boundary="fill",
        fillvalue=0,
    ).astype(np.int16)


# ---------------------------------------------------------------------------
# Public extractor
# ---------------------------------------------------------------------------


class TopologyExtractor:
    """Pure-tensor topology extractor.

    Parameters
    ----------
    solid_tile_ids : Iterable[int], optional
        Tile-ids treated as full collision-solid cells.  Defaults to
        :data:`_DEFAULT_SOLID_IDS`.
    platform_tile_ids : Iterable[int], optional
        Tile-ids treated as one-way platforms.  Defaults to
        :data:`_DEFAULT_PLATFORM_IDS`.
    """

    def __init__(
        self,
        *,
        solid_tile_ids: Optional[Iterable[int]] = None,
        platform_tile_ids: Optional[Iterable[int]] = None,
    ) -> None:
        self._solid_ids: frozenset[int] = frozenset(
            int(v) for v in (solid_tile_ids if solid_tile_ids is not None else _DEFAULT_SOLID_IDS)
        )
        self._platform_ids: frozenset[int] = frozenset(
            int(v)
            for v in (platform_tile_ids if platform_tile_ids is not None else _DEFAULT_PLATFORM_IDS)
        )

    # ------------------------------------------------------------------ API

    def extract(
        self,
        logical_grid: np.ndarray | Sequence[Sequence[int]],
    ) -> TopologyExtractionResult:
        """Run the full extraction pipeline on ``logical_grid``."""
        t0 = time.perf_counter()
        grid = self._coerce_grid(logical_grid)
        rows, cols = grid.shape

        is_solid = self._build_solid_mask(grid)
        is_platform = self._build_platform_mask(grid)
        is_empty = ~is_solid

        # ---- Walkable surfaces & collision boundaries ------------------
        is_walkable_surface = self._detect_walkable_surfaces(is_solid, is_platform)
        is_collision_boundary = self._detect_collision_boundaries(is_solid)

        # ---- Vectorised surface normals --------------------------------
        normal_x, normal_y = self._compute_surface_normals(is_solid)

        # ---- Connected components on walkable surfaces -----------------
        cc_labels, cc_count = ndimage.label(
            is_walkable_surface,
            structure=np.ones((3, 3), dtype=np.int8),
        )

        tensors = TopologyTensors(
            is_solid=is_solid,
            is_empty=is_empty,
            is_walkable_surface=is_walkable_surface,
            is_collision_boundary=is_collision_boundary,
            surface_normal_x=normal_x.astype(np.float32, copy=False),
            surface_normal_y=normal_y.astype(np.float32, copy=False),
            connected_components=cc_labels.astype(np.int32, copy=False),
            component_count=int(cc_count),
        )

        # ---- Anchors via convolutional pattern matching ----------------
        anchors = self._extract_anchors(is_solid, is_empty, is_platform)

        # ---- Lanes from connected components ---------------------------
        lanes = self._build_traversal_lanes(
            cc_labels=cc_labels,
            cc_count=int(cc_count),
            is_platform=is_platform,
            grid_rows=int(rows),
        )

        wall_ms = (time.perf_counter() - t0) * 1000.0

        return TopologyExtractionResult(
            tensors=tensors,
            anchors=tuple(anchors),
            lanes=tuple(lanes),
            grid_rows=int(rows),
            grid_cols=int(cols),
            extraction_wall_ms=float(wall_ms),
        )

    # ---------------------------------------------------------------- masks

    def _coerce_grid(self, logical_grid: np.ndarray | Sequence[Sequence[int]]) -> np.ndarray:
        if isinstance(logical_grid, np.ndarray):
            arr = logical_grid
        else:
            arr = np.asarray(logical_grid)
        if arr.ndim != 2:
            raise ValueError(
                f"TopologyExtractor expects a 2D grid; got shape {arr.shape!r}"
            )
        return arr.astype(np.int32, copy=False)

    def _build_solid_mask(self, grid: np.ndarray) -> np.ndarray:
        """Return a bool mask of *fully solid* cells via vectorised isin."""
        if not self._solid_ids:
            return np.zeros(grid.shape, dtype=bool)
        return np.isin(grid, np.fromiter(self._solid_ids, dtype=np.int32))

    def _build_platform_mask(self, grid: np.ndarray) -> np.ndarray:
        if not self._platform_ids:
            return np.zeros(grid.shape, dtype=bool)
        return np.isin(grid, np.fromiter(self._platform_ids, dtype=np.int32))

    # ------------------------------------------------------- feature lanes

    def _detect_walkable_surfaces(
        self,
        is_solid: np.ndarray,
        is_platform: np.ndarray,
    ) -> np.ndarray:
        """A walkable surface is a solid (or platform) cell whose **upper**
        neighbour is empty.  Implemented via a single shifted comparison:
        no Python-level loops anywhere.
        """
        rows = is_solid.shape[0]
        upper_empty = np.ones_like(is_solid, dtype=bool)
        if rows >= 2:
            # Row r is walkable if row r-1 is empty (the cell above is air).
            upper_empty[1:, :] = ~is_solid[:-1, :]
        # Top row's "above" is implicitly empty (out-of-grid → air).
        return (is_solid | is_platform) & upper_empty

    def _detect_collision_boundaries(self, is_solid: np.ndarray) -> np.ndarray:
        """Boundary = solid cell with at least one empty 4-neighbour."""
        if not is_solid.any():
            return np.zeros_like(is_solid, dtype=bool)
        # convolve the *empty* mask with the cross kernel; any positive
        # value at a solid cell means at least one cardinal neighbour is
        # empty → that solid cell is on the collision boundary.
        is_empty = ~is_solid
        empty_cross = _convolve_uint8(is_empty, _K_CROSS) > 0
        return is_solid & empty_cross

    def _compute_surface_normals(
        self, is_solid: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Approximate outward-pointing normals via the gradient of the
        binary solid mask.  Cells deep inside solid bodies receive
        ``(0, 0)``; collision boundary cells receive a non-zero unit
        vector.
        """
        solid_f = is_solid.astype(np.float32)
        # np.gradient returns (d/drow, d/dcol).  We flip d/drow because in
        # the project's grid convention row index increases *downward*,
        # but the world-space Y axis is up.
        grad_row, grad_col = np.gradient(solid_f)
        nx = -grad_col          # +x in world space = +col in grid
        ny = grad_row           # invert because world Y is up
        # Normalise; cells with zero gradient become (0, 0).
        norm = np.sqrt(nx * nx + ny * ny)
        with np.errstate(divide="ignore", invalid="ignore"):
            nx_unit = np.where(norm > 0, nx / norm, 0.0).astype(np.float32)
            ny_unit = np.where(norm > 0, ny / norm, 0.0).astype(np.float32)
        return nx_unit, ny_unit

    # ----------------------------------------------------------- anchors

    def _extract_anchors(
        self,
        is_solid: np.ndarray,
        is_empty: np.ndarray,
        is_platform: np.ndarray,
    ) -> list[SemanticAnchor]:
        """Run vectorised convolutional pattern matching and emit anchors.

        All ``np.argwhere`` calls return an array of ``(row, col)``
        coordinates; the actual anchor list construction is the only
        Python-level loop in the module, and it runs once per *anchor
        cell*, not once per grid cell.
        """
        rows, cols = is_solid.shape
        anchors: list[SemanticAnchor] = []
        if rows == 0 or cols == 0:
            return anchors

        # ---- Floor-top anchors ---------------------------------------
        # walkable surface cells whose immediate neighbour above is empty.
        walkable = self._detect_walkable_surfaces(is_solid, is_platform)
        floor_top_mask = walkable.copy()
        # Suppress the very top row (no clear "above" semantics) — those
        # cells stay because "above" is implicit air, which is fine.
        floor_top_indices = np.argwhere(floor_top_mask)
        for r, c in floor_top_indices:
            anchors.append(
                SemanticAnchor(
                    x=float(c),
                    y=float(rows - 1 - r),  # Y-up
                    anchor_type=ANCHOR_FLOOR_TOP,
                    normal_x=0.0,
                    normal_y=1.0,
                    up_x=0.0,
                    up_y=1.0,
                    properties={"is_platform": bool(is_platform[r, c])},
                )
            )

        # ---- Ceiling anchors -----------------------------------------
        # Empty cells whose *upper* neighbour is solid.
        ceiling_mask = np.zeros_like(is_solid, dtype=bool)
        if rows >= 2:
            ceiling_mask[1:, :] = is_empty[1:, :] & is_solid[:-1, :]
        for r, c in np.argwhere(ceiling_mask):
            anchors.append(
                SemanticAnchor(
                    x=float(c),
                    y=float(rows - 1 - r),
                    anchor_type=ANCHOR_CEILING,
                    normal_x=0.0,
                    normal_y=-1.0,
                    up_x=0.0,
                    up_y=-1.0,
                )
            )

        # ---- Wall anchors --------------------------------------------
        # Wall-left anchor: empty cell whose RIGHT neighbour is solid →
        # the wall surface faces left, so the anchor's outward normal is
        # (-1, 0).  Wall-right is the symmetric case.
        if cols >= 2:
            wall_left_mask = np.zeros_like(is_solid, dtype=bool)
            wall_left_mask[:, :-1] = is_empty[:, :-1] & is_solid[:, 1:]
            for r, c in np.argwhere(wall_left_mask):
                anchors.append(
                    SemanticAnchor(
                        x=float(c),
                        y=float(rows - 1 - r),
                        anchor_type=ANCHOR_WALL_LEFT,
                        normal_x=-1.0,
                        normal_y=0.0,
                        up_x=0.0,
                        up_y=1.0,
                    )
                )

            wall_right_mask = np.zeros_like(is_solid, dtype=bool)
            wall_right_mask[:, 1:] = is_empty[:, 1:] & is_solid[:, :-1]
            for r, c in np.argwhere(wall_right_mask):
                anchors.append(
                    SemanticAnchor(
                        x=float(c),
                        y=float(rows - 1 - r),
                        anchor_type=ANCHOR_WALL_RIGHT,
                        normal_x=1.0,
                        normal_y=0.0,
                        up_x=0.0,
                        up_y=1.0,
                    )
                )

        # ---- Convex / concave corners --------------------------------
        # A solid cell is a convex outer corner if it has exactly two
        # *adjacent* empty cardinal neighbours (top+left, top+right, ...).
        # Compute via the cross convolution: empty_cross stores the
        # number of empty cardinal neighbours per cell.
        empty_cross_counts = _convolve_uint8(is_empty, _K_CROSS)
        convex_mask = is_solid & (empty_cross_counts == 2)
        for r, c in np.argwhere(convex_mask):
            anchors.append(
                SemanticAnchor(
                    x=float(c),
                    y=float(rows - 1 - r),
                    anchor_type=ANCHOR_CORNER_CONVEX,
                    normal_x=0.0,
                    normal_y=1.0,
                    up_x=0.0,
                    up_y=1.0,
                )
            )

        # Concave inner corner: empty cell whose two *perpendicular*
        # cardinal neighbours are solid.  We use the cross convolution on
        # ``is_solid`` and require count >= 2 plus the cell itself being
        # empty.
        solid_cross_counts = _convolve_uint8(is_solid, _K_CROSS)
        concave_mask = is_empty & (solid_cross_counts >= 2)
        for r, c in np.argwhere(concave_mask):
            anchors.append(
                SemanticAnchor(
                    x=float(c),
                    y=float(rows - 1 - r),
                    anchor_type=ANCHOR_CORNER_CONCAVE,
                    normal_x=0.0,
                    normal_y=1.0,
                    up_x=0.0,
                    up_y=1.0,
                )
            )

        # ---- Slope anchors -------------------------------------------
        # Match the diagonal stair-step kernels.  A cell is a slope
        # anchor if the 3x3 neighbourhood of the solid mask matches one
        # of the two anti-diagonal patterns.  We check the convolution
        # equals 3 (the kernel sum) at exactly that cell.
        slope_ne_score = _convolve_uint8(is_solid, _K_SLOPE_NE)
        slope_nw_score = _convolve_uint8(is_solid, _K_SLOPE_NW)
        slope_mask = (slope_ne_score == 3) | (slope_nw_score == 3)
        # Only emit slope anchors at the empty cell *above* the staircase
        # (the rideable surface).
        slope_anchor_mask = np.zeros_like(is_solid, dtype=bool)
        if rows >= 2:
            slope_anchor_mask[1:, :] = is_empty[1:, :] & slope_mask[:-1, :]
        for r, c in np.argwhere(slope_anchor_mask):
            anchors.append(
                SemanticAnchor(
                    x=float(c),
                    y=float(rows - 1 - r),
                    anchor_type=ANCHOR_SLOPE,
                    normal_x=0.0,
                    normal_y=1.0,
                    up_x=0.7071,
                    up_y=0.7071,
                )
            )

        return anchors

    # ----------------------------------------------------------- lanes

    def _build_traversal_lanes(
        self,
        *,
        cc_labels: np.ndarray,
        cc_count: int,
        is_platform: np.ndarray,
        grid_rows: int,
    ) -> list[TraversalLane]:
        """Convert connected component labels into TraversalLane records.

        Implementation notes
        --------------------
        * ``ndimage.find_objects`` returns slice objects per label
          *without* a Python-level pass over each labelled cell.
        * The cell index list is bounded by ``area`` and remains O(N) in
          the *labelled cells*, never O(rows * cols).
        """
        lanes: list[TraversalLane] = []
        if cc_count == 0:
            return lanes

        slices = ndimage.find_objects(cc_labels)
        for lane_id, sl in enumerate(slices, start=1):
            if sl is None:
                continue
            sub = cc_labels[sl] == lane_id
            area = int(sub.sum())
            if area == 0:
                continue
            # Bounds in original grid coordinates.
            row_slice, col_slice = sl
            min_r, max_r = int(row_slice.start), int(row_slice.stop) - 1
            min_c, max_c = int(col_slice.start), int(col_slice.stop) - 1

            # Cell indices of this lane (vectorised argwhere).
            local_indices = np.argwhere(sub)
            global_indices = local_indices + np.array([min_r, min_c], dtype=np.int64)
            cells = tuple((int(r), int(c)) for r, c in global_indices)

            # Centroid in world tile units (Y-up).
            centroid_row = float(global_indices[:, 0].mean())
            centroid_col = float(global_indices[:, 1].mean())
            centroid_x = centroid_col
            centroid_y = float(grid_rows - 1) - centroid_row

            # Surface kind: prefer "platform" if the majority of the
            # lane's cells are platform-typed, otherwise "floor".  Slope
            # detection is heuristic: a thin diagonal lane (max(width,
            # height) ≈ area) is treated as a slope.
            platform_count = int(is_platform[sl][sub].sum())
            width = max_c - min_c + 1
            height = max_r - min_r + 1
            if platform_count > area * 0.5:
                kind = SURFACE_PLATFORM
            elif (width >= 3 and height >= 3) and area <= max(width, height) + 1:
                kind = SURFACE_SLOPE
            else:
                kind = SURFACE_FLOOR

            lanes.append(
                TraversalLane(
                    lane_id=int(lane_id),
                    surface_kind=kind,
                    bounds=(min_c, min_r, max_c, max_r),
                    area=area,
                    centroid_x=centroid_x,
                    centroid_y=centroid_y,
                    cell_indices=cells,
                )
            )

        return lanes


__all__ = ["TopologyExtractor"]
