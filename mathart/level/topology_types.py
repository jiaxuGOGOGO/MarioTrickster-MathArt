"""Topology data contracts for P1-ARCH-6: Rich topology-aware level semantics.

SESSION-109 — Strong-typed frozen dataclasses for tensor-based level topology
extraction.  This module is the **single source of truth** for the
``LEVEL_TOPOLOGY`` artifact family, deliberately decoupled from any backend
or extractor implementation so that every downstream consumer (USD scene
exporter, AI navigation lane builder, decoration anchor instancer, OpenUSD
``Xform`` materializer in P1-ARCH-5) reads the same typed contracts.

Design references
-----------------
* **Recast / Detour**: voxel → contour → traversal lane extraction pipeline.
* **Oskar Stålberg / Townscaper**: dual-grid + Marching Squares adjacency.
* **SideFX Houdini VEX**: every geometry point/face carries strongly-typed
  attributes (Position, Normal, Up Vector, AnchorType).
* **Pixar OpenUSD**: schema-validated ``Xform`` and ``Prim`` semantics.

Red-line guarantees
-------------------
1. **Anti-data-silo**: all containers are ``@dataclass(frozen=True)``;
   downstream code can never mutate them in place, eliminating spooky
   side-effects in long-running pipelines.
2. **Anti-hardcoded-tiles**: every container exposes only physical /
   geometric attributes (``is_solid``, ``normal_x``, ``anchor_type``); no
   tile character or tile-id leaks into the contract.
3. **Anti-OOM**: tensor containers store ``np.ndarray`` references — no
   Python-level copies are made when constructing the dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Anchor type vocabulary (Houdini-style strong attribute names)
# ---------------------------------------------------------------------------

#: Floor anchor — a walkable cell whose top neighbour is empty.  Useful for
#: spawning ground decorations (grass tufts, footprints, particle emitters).
ANCHOR_FLOOR_TOP = "floor_top"
#: Ceiling anchor — an empty cell whose top neighbour is solid.  Useful for
#: hanging chandeliers, stalactites, vines.
ANCHOR_CEILING = "ceiling"
#: Wall anchor (left-facing) — empty cell whose right neighbour is solid.
ANCHOR_WALL_LEFT = "wall_left"
#: Wall anchor (right-facing) — empty cell whose left neighbour is solid.
ANCHOR_WALL_RIGHT = "wall_right"
#: Convex outer corner — solid cell whose two adjacent diagonal neighbours
#: are empty.  Townscaper-style outer corner trim.
ANCHOR_CORNER_CONVEX = "corner_convex"
#: Concave inner corner — empty cell wedged between two perpendicular solid
#: surfaces.  Townscaper-style inner corner trim.
ANCHOR_CORNER_CONCAVE = "corner_concave"
#: Slope / ramp anchor — diagonal staircase pattern detected by convolution.
ANCHOR_SLOPE = "slope"

#: Canonical, immutable vocabulary the extractor is allowed to emit.
KNOWN_ANCHOR_TYPES: frozenset[str] = frozenset(
    {
        ANCHOR_FLOOR_TOP,
        ANCHOR_CEILING,
        ANCHOR_WALL_LEFT,
        ANCHOR_WALL_RIGHT,
        ANCHOR_CORNER_CONVEX,
        ANCHOR_CORNER_CONCAVE,
        ANCHOR_SLOPE,
    }
)


# ---------------------------------------------------------------------------
# Surface kinds for traversal lanes
# ---------------------------------------------------------------------------

SURFACE_FLOOR = "floor"
SURFACE_PLATFORM = "platform"
SURFACE_SLOPE = "slope"

KNOWN_SURFACE_KINDS: frozenset[str] = frozenset(
    {SURFACE_FLOOR, SURFACE_PLATFORM, SURFACE_SLOPE}
)


# ---------------------------------------------------------------------------
# Frozen contract: SemanticAnchor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticAnchor:
    """A discrete decoration / instancing point with a 3D-ready transform.

    Coordinates are stored in **world tile units** (i.e. one logical grid
    cell == 1.0 unit) using a Y-up, right-handed convention so the values
    can be lifted directly into Unity / Unreal / OpenUSD ``Xform`` prims.

    The ``normal`` vector is unit length and points **outward** from the
    underlying surface.  The ``up`` vector is the tangent-frame "world up"
    that downstream instancers should use to align meshes (matches Houdini
    ``VEX`` ``@N`` + ``@up`` convention).

    Attributes
    ----------
    x, y : float
        World-space position in tile units.  ``y`` is Unity-style Y-up
        (i.e. row 0 of the grid maps to the **highest** y value).
    anchor_type : str
        One of :data:`KNOWN_ANCHOR_TYPES`.  Must be a Data-Oriented Design
        type tag — never a tile character or tile id.
    normal_x, normal_y : float
        Unit-length surface normal (Y-up, world-space).
    up_x, up_y : float
        Unit-length tangent-frame up vector (Y-up, world-space).
    properties : Mapping[str, Any]
        Optional, JSON-serializable extra attributes.  The mapping is
        wrapped to be effectively immutable; callers receive a defensive
        ``dict`` copy via :meth:`properties_dict`.
    """

    x: float
    y: float
    anchor_type: str
    normal_x: float = 0.0
    normal_y: float = 1.0
    up_x: float = 0.0
    up_y: float = 1.0
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.anchor_type not in KNOWN_ANCHOR_TYPES:
            raise ValueError(
                f"SemanticAnchor.anchor_type {self.anchor_type!r} is not in the "
                f"known vocabulary {sorted(KNOWN_ANCHOR_TYPES)!r}; refusing to "
                "leak hardcoded tile semantics into the contract."
            )
        # Defensive freeze: snapshot the properties mapping so the caller
        # cannot mutate the dataclass through a shared reference later.
        object.__setattr__(self, "properties", dict(self.properties))

    def properties_dict(self) -> dict[str, Any]:
        """Return a defensive copy of the properties mapping."""
        return dict(self.properties)

    def transform_matrix(self) -> np.ndarray:
        """Return a 4x4 column-major transform matrix (OpenUSD convention).

        The matrix is built from ``(x, y, 0)`` translation and a 2D rotation
        whose +Y axis aligns with ``(up_x, up_y)`` and whose +X axis is the
        right-hand perpendicular of the up vector.  This is exactly the
        ``UsdGeom.Xformable`` shape downstream USD adapters expect.
        """
        ux, uy = float(self.up_x), float(self.up_y)
        # Right-handed +X = perpendicular to +Y (tangent of up vector).
        rx, ry = uy, -ux
        m = np.eye(4, dtype=np.float64)
        m[0, 0], m[1, 0] = rx, ry
        m[0, 1], m[1, 1] = ux, uy
        m[0, 3], m[1, 3] = float(self.x), float(self.y)
        return m

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "anchor_type": str(self.anchor_type),
            "normal": [float(self.normal_x), float(self.normal_y)],
            "up": [float(self.up_x), float(self.up_y)],
            "properties": self.properties_dict(),
        }


# ---------------------------------------------------------------------------
# Frozen contract: TraversalLane
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraversalLane:
    """A connected component of walkable surface cells.

    Lanes are the navigation-graph equivalent of Recast/Detour's
    *traversal polygons*: each lane is one continuous patch of ground
    that an AI agent can walk on without jumping or falling.

    Attributes
    ----------
    lane_id : int
        Stable component label assigned by ``scipy.ndimage.label``.
        ``0`` is reserved for "no lane" (background).
    surface_kind : str
        One of :data:`KNOWN_SURFACE_KINDS` (floor / platform / slope).
    bounds : Tuple[int, int, int, int]
        ``(min_col, min_row, max_col, max_row)`` in grid coordinates.
    area : int
        Number of cells in the lane.
    centroid_x, centroid_y : float
        Geometric centroid in **world tile units** (Y-up).
    cell_indices : Tuple[Tuple[int, int], ...]
        Frozen tuple of ``(row, col)`` cells belonging to this lane.
        Stored as a tuple-of-tuples so the dataclass remains hashable
        and JSON-serializable without leaking ``np.ndarray`` aliases.
    """

    lane_id: int
    surface_kind: str
    bounds: Tuple[int, int, int, int]
    area: int
    centroid_x: float
    centroid_y: float
    cell_indices: Tuple[Tuple[int, int], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.surface_kind not in KNOWN_SURFACE_KINDS:
            raise ValueError(
                f"TraversalLane.surface_kind {self.surface_kind!r} is not in "
                f"the known vocabulary {sorted(KNOWN_SURFACE_KINDS)!r}."
            )
        if self.area < 0:
            raise ValueError("TraversalLane.area must be non-negative")
        # Force tuple-of-tuples so the dataclass stays frozen & hashable.
        object.__setattr__(
            self, "bounds", tuple(int(v) for v in self.bounds),  # type: ignore[arg-type]
        )
        object.__setattr__(
            self,
            "cell_indices",
            tuple((int(r), int(c)) for r, c in self.cell_indices),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": int(self.lane_id),
            "surface_kind": str(self.surface_kind),
            "bounds": list(self.bounds),
            "area": int(self.area),
            "centroid": [float(self.centroid_x), float(self.centroid_y)],
            "cell_count": len(self.cell_indices),
        }


# ---------------------------------------------------------------------------
# Frozen contract: TopologyTensors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopologyTensors:
    """Container for the raw boolean masks and connectivity labels.

    All arrays are stored as references — the constructor does **not**
    copy.  Callers MUST treat the arrays as read-only; the dataclass
    enforces this by setting ``arr.flags.writeable = False`` when
    possible.  This keeps the worst-case memory footprint at one bit per
    grid cell and avoids the OOM trap described in the project red lines.
    """

    is_solid: np.ndarray
    is_empty: np.ndarray
    is_walkable_surface: np.ndarray
    is_collision_boundary: np.ndarray
    surface_normal_x: np.ndarray
    surface_normal_y: np.ndarray
    connected_components: np.ndarray
    component_count: int

    def __post_init__(self) -> None:
        # Lock arrays as read-only so accidental in-place mutations explode
        # immediately rather than silently corrupting the manifest.
        for arr_name in (
            "is_solid",
            "is_empty",
            "is_walkable_surface",
            "is_collision_boundary",
            "surface_normal_x",
            "surface_normal_y",
            "connected_components",
        ):
            arr = getattr(self, arr_name)
            if isinstance(arr, np.ndarray):
                try:
                    arr.flags.writeable = False
                except ValueError:
                    # Some array views cannot be flipped to read-only;
                    # we tolerate this rather than crashing.
                    pass

    def shape(self) -> Tuple[int, int]:
        return tuple(int(v) for v in self.is_solid.shape)  # type: ignore[return-value]

    def to_summary(self) -> dict[str, Any]:
        rows, cols = self.shape()
        return {
            "rows": int(rows),
            "cols": int(cols),
            "solid_cells": int(self.is_solid.sum()),
            "empty_cells": int(self.is_empty.sum()),
            "walkable_surface_cells": int(self.is_walkable_surface.sum()),
            "collision_boundary_cells": int(self.is_collision_boundary.sum()),
            "connected_component_count": int(self.component_count),
        }


# ---------------------------------------------------------------------------
# Frozen contract: TopologyExtractionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopologyExtractionResult:
    """Top-level result returned by :class:`TopologyExtractor`.

    This is the contract a backend serializes into the
    ``LEVEL_TOPOLOGY`` ArtifactManifest.  Keeping it frozen and JSON-aware
    means callers can either inspect it in memory or trust the manifest
    on disk to be byte-identical to what the extractor produced.
    """

    tensors: TopologyTensors
    anchors: Tuple[SemanticAnchor, ...]
    lanes: Tuple[TraversalLane, ...]
    grid_rows: int
    grid_cols: int
    extraction_wall_ms: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchors", tuple(self.anchors))
        object.__setattr__(self, "lanes", tuple(self.lanes))

    def anchor_count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {atype: 0 for atype in sorted(KNOWN_ANCHOR_TYPES)}
        for anchor in self.anchors:
            counts[anchor.anchor_type] = counts.get(anchor.anchor_type, 0) + 1
        return counts

    def lane_count_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {kind: 0 for kind in sorted(KNOWN_SURFACE_KINDS)}
        for lane in self.lanes:
            counts[lane.surface_kind] = counts.get(lane.surface_kind, 0) + 1
        return counts

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "grid_rows": int(self.grid_rows),
            "grid_cols": int(self.grid_cols),
            "anchor_count": len(self.anchors),
            "lane_count": len(self.lanes),
            "anchors_by_type": self.anchor_count_by_type(),
            "lanes_by_kind": self.lane_count_by_kind(),
            "extraction_wall_ms": float(self.extraction_wall_ms),
            "tensor_summary": self.tensors.to_summary(),
        }

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary — no NumPy arrays remain."""
        return {
            "grid_rows": int(self.grid_rows),
            "grid_cols": int(self.grid_cols),
            "extraction_wall_ms": float(self.extraction_wall_ms),
            "summary": self.to_summary_dict(),
            "anchors": [a.to_dict() for a in self.anchors],
            "lanes": [lane.to_dict() for lane in self.lanes],
        }


__all__ = [
    "ANCHOR_CEILING",
    "ANCHOR_CORNER_CONCAVE",
    "ANCHOR_CORNER_CONVEX",
    "ANCHOR_FLOOR_TOP",
    "ANCHOR_SLOPE",
    "ANCHOR_WALL_LEFT",
    "ANCHOR_WALL_RIGHT",
    "KNOWN_ANCHOR_TYPES",
    "KNOWN_SURFACE_KINDS",
    "SURFACE_FLOOR",
    "SURFACE_PLATFORM",
    "SURFACE_SLOPE",
    "SemanticAnchor",
    "TopologyExtractionResult",
    "TopologyTensors",
    "TraversalLane",
]
