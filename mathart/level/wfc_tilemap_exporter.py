"""
WFC-to-Unity Tilemap Exporter with Dual Grid Mapping.

SESSION-062: Phase 4 — Environment Closed-Loop & Content Volume.

This module bridges the gap between the Python-side WFC level generator
and Unity's Tilemap system.  It converts ASCII grid output from
``ConstraintAwareWFC`` into structured JSON that a Unity C# loader can
instantiate as ``RuleTile`` tilemaps with ``CompositeCollider2D``.

The key innovation is the **Dual Grid Mapper** — inspired by Oskar
Stålberg's *Townscaper* / *Bad North* technique and Boris the Brave's
analysis of quarter-tile autotiling.  Instead of placing visual tiles
directly on the logical grid, we compute a secondary "dual" grid offset
by half a tile.  Each dual-grid cell's visual tile is selected via a
4-bit Marching Squares index derived from the four surrounding logical
cells.  This produces organic, seamless terrain edges using a minimal
set of only 16 tile variants (or 6 with rotation).

Research foundations:
  1. **Maxim Gumin — WFC (2016)**: Observe / Collapse / Propagate
     constraint solver.  Our ``ConstraintAwareWFC`` already implements
     this with physics-based TTC vetoes.
  2. **Oskar Stålberg — Dual Grid WFC (2017–2021)**: Cut tiles along
     the dual grid instead of the main grid.  Marching Squares on
     vertex data → organic edges with minimal tileset.
  3. **Boris the Brave — Quarter-Tile Autotiling (2023)**: Formal
     analysis of dual-grid vs quarter-tile tradeoffs; precomposition
     into 48 blob-pattern tiles.
  4. **Unity Tilemap + CompositeCollider2D**: Engine-native 2D tile
     placement with merged physics colliders.

Architecture::

    ConstraintAwareWFC.generate()
        ↓  ASCII string
    parse_fragment()
        ↓  List[List[str]]
    WFCTilemapExporter
        ├─ _ascii_to_tile_ids()     → 2D int grid (logical)
        ├─ DualGridMapper.compute() → 2D int grid (visual, 16 variants)
        ├─ _embed_metadata()        → physics + reachability info
        └─ export_tilemap_json()    → tilemap_data.json

    Unity side:
    WFCTilemapLoader.cs
        ├─ Load tilemap_data.json
        ├─ Instantiate RuleTile per cell (visual layer)
        ├─ Instantiate collider tiles (logical layer)
        └─ Attach CompositeCollider2D
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Sequence

from .templates import (
    ELEMENT_MAP,
    SOLID_CHARS,
    PLATFORM_CHARS,
    HAZARD_CHARS,
    ENEMY_CHARS,
    SPAWN_CHARS,
    SPECIAL_CHARS,
    AIR_CHAR,
    parse_fragment,
)
from .constraint_wfc import (
    ConstraintAwareWFC,
    PhysicsConstraint,
    ReachabilityValidator,
    TilePlatformExtractor,
)


# ── Tile ID Constants ────────────────────────────────────────────────────────

# Logical tile IDs (what the game logic sees)
TILE_ID_AIR = 0
TILE_ID_SOLID = 1
TILE_ID_PLATFORM = 2
TILE_ID_HAZARD = 3
TILE_ID_ENEMY = 4
TILE_ID_SPAWN = 5
TILE_ID_GOAL = 6
TILE_ID_COLLECTIBLE = 7
TILE_ID_BOUNCE = 8
TILE_ID_COLLAPSE = 9
TILE_ID_MOVING = 10
TILE_ID_ONE_WAY = 11
TILE_ID_WALL = 12
TILE_ID_FAKE_WALL = 13
TILE_ID_HIDDEN = 14

# Char → tile ID mapping
_CHAR_TO_TILE_ID: dict[str, int] = {
    ".": TILE_ID_AIR,
    "#": TILE_ID_SOLID,
    "=": TILE_ID_PLATFORM,
    "^": TILE_ID_HAZARD,
    "~": TILE_ID_HAZARD,
    "P": TILE_ID_HAZARD,
    "E": TILE_ID_ENEMY,
    "e": TILE_ID_ENEMY,
    "M": TILE_ID_SPAWN,
    "T": TILE_ID_SPAWN,
    "G": TILE_ID_GOAL,
    "o": TILE_ID_COLLECTIBLE,
    "B": TILE_ID_BOUNCE,
    "C": TILE_ID_COLLAPSE,
    ">": TILE_ID_MOVING,
    "-": TILE_ID_ONE_WAY,
    "W": TILE_ID_WALL,
    "F": TILE_ID_FAKE_WALL,
    "H": TILE_ID_HIDDEN,
}


# ── Marching Squares Dual Grid ──────────────────────────────────────────────


@dataclass
class DualGridCell:
    """A single cell in the dual (visual) grid.

    Attributes:
        marching_index: 4-bit Marching Squares index (0–15).
            Bit layout (Stålberg convention):
                bit 0 = top-left corner     (base cell [r, c])
                bit 1 = top-right corner    (base cell [r, c+1])
                bit 2 = bottom-right corner (base cell [r+1, c+1])
                bit 3 = bottom-left corner  (base cell [r+1, c])
            0 = all air, 15 = all solid.
        terrain_type: dominant terrain type string for themed tilesets.
    """
    marching_index: int = 0
    terrain_type: str = "ground"


class DualGridMapper:
    """Compute the dual (visual) grid from a logical tile-ID grid.

    The dual grid has dimensions ``(rows-1, cols-1)`` relative to the
    logical grid.  Each dual cell sits at the intersection of four
    logical cells and receives a 4-bit Marching Squares index.

    This implements Oskar Stålberg's dual-grid technique:
    > "Cut tiles along the dual grid instead of the main grid."
    > — @OskSta, October 13, 2021

    With only 16 tile variants (or 6 with rotational symmetry), the
    dual grid produces organic, seamless terrain edges.
    """

    # Tile IDs considered "solid" for Marching Squares purposes
    SOLID_IDS: frozenset[int] = frozenset({
        TILE_ID_SOLID, TILE_ID_WALL, TILE_ID_FAKE_WALL,
        TILE_ID_PLATFORM, TILE_ID_BOUNCE, TILE_ID_COLLAPSE,
        TILE_ID_MOVING, TILE_ID_ONE_WAY,
    })

    @classmethod
    def compute(
        cls,
        logical_grid: list[list[int]],
        *,
        terrain_types: Optional[list[list[str]]] = None,
    ) -> list[list[DualGridCell]]:
        """Compute the dual grid from a logical tile-ID grid.

        Parameters
        ----------
        logical_grid : list[list[int]]
            2D grid of logical tile IDs.
        terrain_types : list[list[str]], optional
            Per-cell terrain type strings for themed tilesets.

        Returns
        -------
        list[list[DualGridCell]]
            Dual grid of dimensions ``(rows-1, cols-1)``.
        """
        rows = len(logical_grid)
        if rows < 2:
            return []
        cols = len(logical_grid[0]) if rows > 0 else 0
        if cols < 2:
            return []

        dual_rows = rows - 1
        dual_cols = cols - 1
        dual_grid: list[list[DualGridCell]] = []

        for dr in range(dual_rows):
            row: list[DualGridCell] = []
            for dc in range(dual_cols):
                # Four corners of this dual cell
                tl = logical_grid[dr][dc]          # top-left
                tr = logical_grid[dr][dc + 1]      # top-right
                br = logical_grid[dr + 1][dc + 1]  # bottom-right
                bl = logical_grid[dr + 1][dc]      # bottom-left

                # Compute 4-bit Marching Squares index
                index = 0
                if tl in cls.SOLID_IDS:
                    index |= 1  # bit 0
                if tr in cls.SOLID_IDS:
                    index |= 2  # bit 1
                if br in cls.SOLID_IDS:
                    index |= 4  # bit 2
                if bl in cls.SOLID_IDS:
                    index |= 8  # bit 3

                # Determine dominant terrain type
                terrain = "ground"
                if terrain_types is not None:
                    corners = [
                        terrain_types[dr][dc],
                        terrain_types[dr][dc + 1],
                        terrain_types[dr + 1][dc + 1],
                        terrain_types[dr + 1][dc],
                    ]
                    # Majority vote
                    from collections import Counter
                    counts = Counter(corners)
                    terrain = counts.most_common(1)[0][0]

                row.append(DualGridCell(marching_index=index, terrain_type=terrain))
            dual_grid.append(row)

        return dual_grid

    @staticmethod
    def marching_index_to_edges(index: int) -> dict[str, bool]:
        """Decode a Marching Squares index into edge flags.

        Useful for debugging and visualization.
        """
        return {
            "top_left_solid": bool(index & 1),
            "top_right_solid": bool(index & 2),
            "bottom_right_solid": bool(index & 4),
            "bottom_left_solid": bool(index & 8),
        }

    @staticmethod
    def rotation_reduced_index(index: int) -> tuple[int, int]:
        """Reduce a 16-variant index to 6 canonical forms + rotation.

        Returns (canonical_index, rotation_degrees).
        Rotation is clockwise: 0, 90, 180, 270.

        The 6 canonical forms are:
          0: all air      (index 0)
          1: one corner   (index 1)
          2: two adjacent (index 3)
          3: two diagonal (index 5)
          4: three corners(index 7)
          5: all solid    (index 15)
        """
        # All 16 indices mapped to (canonical, rotation)
        _REDUCTION_TABLE: dict[int, tuple[int, int]] = {
            0:  (0, 0),
            1:  (1, 0),
            2:  (1, 90),
            3:  (3, 0),
            4:  (1, 180),
            5:  (5, 0),
            6:  (3, 90),
            7:  (7, 0),
            8:  (1, 270),
            9:  (3, 270),
            10: (5, 90),
            11: (7, 270),
            12: (3, 180),
            13: (7, 180),
            14: (7, 90),
            15: (15, 0),
        }
        return _REDUCTION_TABLE.get(index, (index, 0))


# ── Tilemap JSON Export ──────────────────────────────────────────────────────


@dataclass
class TilemapMetadata:
    """Metadata embedded in the exported tilemap JSON."""
    generator: str = "MarioTrickster-MathArt/ConstraintAwareWFC"
    session: str = "SESSION-062"
    physics_gravity: float = 26.0
    physics_max_run_speed: float = 8.5
    physics_jump_velocity: float = 12.0
    physics_max_jump_height: float = 0.0
    physics_max_jump_distance: float = 0.0
    is_playable: bool = False
    reachability_path_length: int = 0
    difficulty_target: float = 0.5
    generation_attempts: int = 0
    veto_count: int = 0
    tile_diversity: float = 0.0
    platform_count: int = 0
    gap_count: int = 0
    dual_grid_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TilemapExportResult:
    """Result of a tilemap export operation."""
    json_path: str = ""
    logical_width: int = 0
    logical_height: int = 0
    dual_width: int = 0
    dual_height: int = 0
    tile_count: int = 0
    unique_tiles: int = 0
    metadata: Optional[TilemapMetadata] = None
    success: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.metadata:
            d["metadata"] = self.metadata.to_dict()
        return d


class WFCTilemapExporter:
    """Export WFC-generated levels to Unity-compatible Tilemap JSON.

    This exporter produces a JSON file containing:
    1. **logical_grid**: 2D array of tile IDs for game logic / colliders.
    2. **dual_grid**: 2D array of Marching Squares indices for visual tiles.
    3. **entity_spawns**: List of entity spawn positions (enemies, items, etc.).
    4. **physics_constraints**: Player physics parameters for runtime use.
    5. **metadata**: Generation statistics and validation results.

    The Unity-side loader (``WFCTilemapLoader.cs``) reads this JSON and:
    - Creates a logical Tilemap with ``CompositeCollider2D`` for physics.
    - Creates a visual Tilemap using ``RuleTile`` driven by dual-grid indices.
    - Spawns entity prefabs at specified positions.

    Usage::

        from mathart.level.wfc_tilemap_exporter import WFCTilemapExporter
        from mathart.level.constraint_wfc import ConstraintAwareWFC

        wfc = ConstraintAwareWFC(seed=42)
        wfc.learn()
        level_str = wfc.generate(width=30, height=8)

        exporter = WFCTilemapExporter()
        result = exporter.export_tilemap_json(level_str, output_dir="./output")
    """

    def __init__(
        self,
        physics: Optional[PhysicsConstraint] = None,
        enable_dual_grid: bool = True,
    ):
        self.physics = physics or PhysicsConstraint.mario_default()
        self.enable_dual_grid = enable_dual_grid
        self.validator = ReachabilityValidator(self.physics)

    def ascii_to_tile_ids(self, grid: list[list[str]]) -> list[list[int]]:
        """Convert an ASCII character grid to a tile-ID grid.

        Each character is mapped to an integer tile ID via
        ``_CHAR_TO_TILE_ID``.  Unknown characters default to
        ``TILE_ID_AIR``.
        """
        return [
            [_CHAR_TO_TILE_ID.get(ch, TILE_ID_AIR) for ch in row]
            for row in grid
        ]

    def extract_entity_spawns(
        self, grid: list[list[str]]
    ) -> list[dict[str, Any]]:
        """Extract entity spawn positions from the ASCII grid.

        Returns a list of dicts with keys:
        - ``type``: entity type string (from ELEMENT_MAP)
        - ``row``, ``col``: grid position
        - ``world_x``, ``world_y``: world-space position (tile units)
        """
        entities: list[dict[str, Any]] = []
        for r, row in enumerate(grid):
            for c, ch in enumerate(row):
                if ch in ENEMY_CHARS | SPAWN_CHARS | SPECIAL_CHARS | {"o"}:
                    entities.append({
                        "type": ELEMENT_MAP.get(ch, "unknown"),
                        "char": ch,
                        "row": r,
                        "col": c,
                        "world_x": float(c),
                        "world_y": float(len(grid) - 1 - r),  # Unity Y-up
                    })
        return entities

    def compute_tile_diversity(self, tile_ids: list[list[int]]) -> float:
        """Compute tile diversity as ratio of unique tiles to total tiles."""
        all_ids: set[int] = set()
        total = 0
        for row in tile_ids:
            for tid in row:
                all_ids.add(tid)
                total += 1
        return len(all_ids) / max(total, 1)

    def export_tilemap_json(
        self,
        level_str: str,
        output_dir: str = ".",
        filename: str = "tilemap_data.json",
        *,
        validate: bool = True,
    ) -> TilemapExportResult:
        """Export a WFC-generated level to Unity Tilemap JSON.

        Parameters
        ----------
        level_str : str
            ASCII level string from ``ConstraintAwareWFC.generate()``.
        output_dir : str
            Directory to write the JSON file.
        filename : str
            Output filename.
        validate : bool
            Whether to run playability validation.

        Returns
        -------
        TilemapExportResult
            Export result with paths and statistics.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Parse ASCII grid
        grid = parse_fragment(level_str)
        rows = len(grid)
        cols = max(len(r) for r in grid) if rows > 0 else 0

        # Normalize grid dimensions (pad short rows)
        for r in range(rows):
            while len(grid[r]) < cols:
                grid[r].append(AIR_CHAR)

        # Convert to tile IDs
        tile_ids = self.ascii_to_tile_ids(grid)

        # Compute dual grid
        dual_data: list[list[int]] = []
        if self.enable_dual_grid and rows >= 2 and cols >= 2:
            dual_cells = DualGridMapper.compute(tile_ids)
            dual_data = [
                [cell.marching_index for cell in row]
                for row in dual_cells
            ]

        # Extract entities
        entities = self.extract_entity_spawns(grid)

        # Validate playability
        is_playable = False
        path_length = 0
        if validate:
            is_playable, path = self.validator.validate_level(grid)
            path_length = len(path)

        # Extract platforms and gaps
        platforms = TilePlatformExtractor.extract_platforms(grid)
        gaps = TilePlatformExtractor.extract_gaps(grid, self.physics)

        # Compute statistics
        diversity = self.compute_tile_diversity(tile_ids)

        # Build metadata
        metadata = TilemapMetadata(
            physics_gravity=self.physics.gravity,
            physics_max_run_speed=self.physics.max_run_speed,
            physics_jump_velocity=self.physics.jump_velocity,
            physics_max_jump_height=self.physics.max_jump_height,
            physics_max_jump_distance=self.physics.max_jump_distance,
            is_playable=is_playable,
            reachability_path_length=path_length,
            tile_diversity=round(diversity, 4),
            platform_count=len(platforms),
            gap_count=len(gaps),
            dual_grid_enabled=self.enable_dual_grid,
        )

        # Build JSON payload
        payload: dict[str, Any] = {
            "version": "1.0.0",
            "generator": "MarioTrickster-MathArt/SESSION-062",
            "width": cols,
            "height": rows,
            "logical_grid": tile_ids,
            "dual_grid": dual_data,
            "dual_grid_width": len(dual_data[0]) if dual_data else 0,
            "dual_grid_height": len(dual_data) if dual_data else 0,
            "entity_spawns": entities,
            "physics_constraints": self.physics.to_dict(),
            "tile_id_legend": {
                str(v): k for k, v in {
                    "air": TILE_ID_AIR,
                    "solid": TILE_ID_SOLID,
                    "platform": TILE_ID_PLATFORM,
                    "hazard": TILE_ID_HAZARD,
                    "enemy": TILE_ID_ENEMY,
                    "spawn": TILE_ID_SPAWN,
                    "goal": TILE_ID_GOAL,
                    "collectible": TILE_ID_COLLECTIBLE,
                    "bounce": TILE_ID_BOUNCE,
                    "collapse": TILE_ID_COLLAPSE,
                    "moving": TILE_ID_MOVING,
                    "one_way": TILE_ID_ONE_WAY,
                    "wall": TILE_ID_WALL,
                    "fake_wall": TILE_ID_FAKE_WALL,
                    "hidden": TILE_ID_HIDDEN,
                }.items()
            },
            "marching_squares_legend": {
                "0": "all_air",
                "1": "corner_tl",
                "2": "corner_tr",
                "3": "edge_top",
                "4": "corner_br",
                "5": "diagonal_tl_br",
                "6": "edge_right",
                "7": "three_no_bl",
                "8": "corner_bl",
                "9": "edge_left",
                "10": "diagonal_tr_bl",
                "11": "three_no_br",
                "12": "edge_bottom",
                "13": "three_no_tr",
                "14": "three_no_tl",
                "15": "all_solid",
            },
            "metadata": metadata.to_dict(),
        }

        # Write JSON
        json_file = out_path / filename
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        # Build result
        unique_ids: set[int] = set()
        total_tiles = 0
        for row in tile_ids:
            for tid in row:
                unique_ids.add(tid)
                total_tiles += 1

        return TilemapExportResult(
            json_path=str(json_file),
            logical_width=cols,
            logical_height=rows,
            dual_width=len(dual_data[0]) if dual_data else 0,
            dual_height=len(dual_data) if dual_data else 0,
            tile_count=total_tiles,
            unique_tiles=len(unique_ids),
            metadata=metadata,
            success=True,
        )

    def export_batch(
        self,
        levels: list[str],
        output_dir: str = ".",
        prefix: str = "level",
    ) -> list[TilemapExportResult]:
        """Export multiple levels to individual JSON files."""
        results: list[TilemapExportResult] = []
        for i, level_str in enumerate(levels):
            filename = f"{prefix}_{i:04d}.json"
            result = self.export_tilemap_json(
                level_str, output_dir=output_dir, filename=filename
            )
            results.append(result)
        return results


# ── Unity C# Loader Generator ───────────────────────────────────────────────


UNITY_WFC_TILEMAP_LOADER_CS = r'''using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Tilemaps;

/// <summary>
/// Loads WFC-generated tilemap JSON and instantiates Unity Tilemaps.
///
/// Research provenance:
///   - Maxim Gumin: WFC constraint solver (2016)
///   - Oskar Stålberg: Dual Grid WFC / Townscaper (2017-2021)
///   - Boris the Brave: Quarter-Tile Autotiling analysis (2023)
///   - Unity: Tilemap + CompositeCollider2D for optimized 2D physics
///
/// Usage:
///   1. Attach this component to a GameObject.
///   2. Assign logicalTilemap, visualTilemap, and tile assets in Inspector.
///   3. Call LoadFromJson(path) or LoadFromTextAsset(textAsset).
/// </summary>
public class WFCTilemapLoader : MonoBehaviour
{
    [Header("Tilemaps")]
    [Tooltip("Tilemap for physics colliders (logical layer)")]
    public Tilemap logicalTilemap;

    [Tooltip("Tilemap for visual rendering (dual grid layer)")]
    public Tilemap visualTilemap;

    [Header("Tile Assets")]
    [Tooltip("Tile used for solid ground colliders")]
    public TileBase solidColliderTile;

    [Tooltip("Tile used for one-way platforms")]
    public TileBase platformColliderTile;

    [Tooltip("16 Marching Squares visual tiles (index 0-15)")]
    public TileBase[] marchingSquaresTiles = new TileBase[16];

    [Header("Entity Prefabs")]
    [Tooltip("Prefab lookup by entity type name")]
    public EntityPrefabEntry[] entityPrefabs;

    [Header("Settings")]
    public Vector2 tileWorldSize = Vector2.one;
    public bool enableDualGrid = true;
    public bool spawnEntities = true;

    [System.Serializable]
    public class EntityPrefabEntry
    {
        public string typeName;
        public GameObject prefab;
    }

    [System.Serializable]
    private class TilemapData
    {
        public int width;
        public int height;
        public int[][] logical_grid;
        public int[][] dual_grid;
        public int dual_grid_width;
        public int dual_grid_height;
        public EntitySpawn[] entity_spawns;
        public PhysicsConstraints physics_constraints;
    }

    [System.Serializable]
    private class EntitySpawn
    {
        public string type;
        public int row;
        public int col;
        public float world_x;
        public float world_y;
    }

    [System.Serializable]
    private class PhysicsConstraints
    {
        public float gravity;
        public float max_run_speed;
        public float jump_velocity;
        public float max_jump_height;
        public float max_jump_distance;
    }

    private readonly Dictionary<string, GameObject> _prefabLookup =
        new Dictionary<string, GameObject>();
    private readonly List<GameObject> _spawnedEntities = new List<GameObject>();

    private void Awake()
    {
        // Build prefab lookup
        if (entityPrefabs != null)
        {
            foreach (var entry in entityPrefabs)
            {
                if (!string.IsNullOrEmpty(entry.typeName) && entry.prefab != null)
                    _prefabLookup[entry.typeName] = entry.prefab;
            }
        }
    }

    /// <summary>Load tilemap from a JSON file path.</summary>
    public void LoadFromJson(string jsonPath)
    {
        if (!File.Exists(jsonPath))
        {
            Debug.LogError($"WFCTilemapLoader: File not found: {jsonPath}");
            return;
        }
        string json = File.ReadAllText(jsonPath);
        LoadFromJsonString(json);
    }

    /// <summary>Load tilemap from a TextAsset.</summary>
    public void LoadFromTextAsset(TextAsset textAsset)
    {
        if (textAsset == null)
        {
            Debug.LogError("WFCTilemapLoader: TextAsset is null");
            return;
        }
        LoadFromJsonString(textAsset.text);
    }

    /// <summary>Load tilemap from a JSON string.</summary>
    public void LoadFromJsonString(string json)
    {
        ClearAll();

        var data = JsonUtility.FromJson<TilemapData>(json);
        if (data == null)
        {
            Debug.LogError("WFCTilemapLoader: Failed to parse JSON");
            return;
        }

        // Place logical tiles (colliders)
        if (logicalTilemap != null && data.logical_grid != null)
        {
            PlaceLogicalTiles(data);
        }

        // Place visual tiles (dual grid)
        if (enableDualGrid && visualTilemap != null && data.dual_grid != null)
        {
            PlaceVisualTiles(data);
        }

        // Spawn entities
        if (spawnEntities && data.entity_spawns != null)
        {
            SpawnEntities(data);
        }

        // Apply physics
        if (data.physics_constraints != null)
        {
            ApplyPhysicsConstraints(data.physics_constraints);
        }

        Debug.Log($"WFCTilemapLoader: Loaded {data.width}x{data.height} level " +
                  $"with {data.entity_spawns?.Length ?? 0} entities");
    }

    private void PlaceLogicalTiles(TilemapData data)
    {
        for (int r = 0; r < data.height; r++)
        {
            if (r >= data.logical_grid.Length) break;
            for (int c = 0; c < data.width; c++)
            {
                if (c >= data.logical_grid[r].Length) break;
                int tileId = data.logical_grid[r][c];

                // Unity Tilemap: Y increases upward, row 0 = top of level
                var pos = new Vector3Int(c, data.height - 1 - r, 0);

                // Solid tiles get collider tiles
                if (tileId == 1 || tileId == 12) // solid or wall
                {
                    if (solidColliderTile != null)
                        logicalTilemap.SetTile(pos, solidColliderTile);
                }
                else if (tileId == 2 || tileId == 8 || tileId == 9 ||
                         tileId == 10 || tileId == 11) // platforms
                {
                    if (platformColliderTile != null)
                        logicalTilemap.SetTile(pos, platformColliderTile);
                }
            }
        }

        // Refresh CompositeCollider2D if present
        var composite = logicalTilemap.GetComponent<CompositeCollider2D>();
        if (composite != null)
        {
            composite.GenerateGeometry();
        }
    }

    private void PlaceVisualTiles(TilemapData data)
    {
        if (data.dual_grid == null) return;

        int dualHeight = data.dual_grid.Length;
        for (int dr = 0; dr < dualHeight; dr++)
        {
            int dualWidth = data.dual_grid[dr].Length;
            for (int dc = 0; dc < dualWidth; dc++)
            {
                int marchingIndex = data.dual_grid[dr][dc];
                if (marchingIndex < 0 || marchingIndex >= marchingSquaresTiles.Length)
                    continue;

                var tile = marchingSquaresTiles[marchingIndex];
                if (tile == null) continue;

                // Dual grid is offset by 0.5 tiles from logical grid
                // Unity position: col + 0.5, (height - 1 - row) + 0.5
                var pos = new Vector3Int(dc, dualHeight - 1 - dr, 0);
                visualTilemap.SetTile(pos, tile);
            }
        }
    }

    private void SpawnEntities(TilemapData data)
    {
        foreach (var spawn in data.entity_spawns)
        {
            if (_prefabLookup.TryGetValue(spawn.type, out var prefab))
            {
                var worldPos = new Vector3(
                    spawn.world_x * tileWorldSize.x,
                    spawn.world_y * tileWorldSize.y,
                    0f
                );
                var entity = Instantiate(prefab, worldPos, Quaternion.identity, transform);
                entity.name = $"{spawn.type}_{spawn.row}_{spawn.col}";
                _spawnedEntities.Add(entity);
            }
        }
    }

    private void ApplyPhysicsConstraints(PhysicsConstraints constraints)
    {
        // Store physics constraints for runtime use
        // Game-specific: override player controller parameters
        PlayerPrefs.SetFloat("WFC_Gravity", constraints.gravity);
        PlayerPrefs.SetFloat("WFC_MaxRunSpeed", constraints.max_run_speed);
        PlayerPrefs.SetFloat("WFC_JumpVelocity", constraints.jump_velocity);
        PlayerPrefs.SetFloat("WFC_MaxJumpHeight", constraints.max_jump_height);
        PlayerPrefs.SetFloat("WFC_MaxJumpDistance", constraints.max_jump_distance);
    }

    /// <summary>Clear all tiles and spawned entities.</summary>
    public void ClearAll()
    {
        if (logicalTilemap != null) logicalTilemap.ClearAllTiles();
        if (visualTilemap != null) visualTilemap.ClearAllTiles();

        foreach (var entity in _spawnedEntities)
        {
            if (entity != null)
                Destroy(entity);
        }
        _spawnedEntities.Clear();
    }
}
'''


def generate_wfc_tilemap_loader(output_dir: str) -> str:
    """Generate the Unity C# WFCTilemapLoader script.

    Parameters
    ----------
    output_dir : str
        Directory to write the C# file.

    Returns
    -------
    str
        Path to the generated C# file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cs_path = out / "WFCTilemapLoader.cs"
    cs_path.write_text(UNITY_WFC_TILEMAP_LOADER_CS.strip(), encoding="utf-8")
    return str(cs_path)


# ── Convenience Functions ────────────────────────────────────────────────────


def generate_and_export_tilemap(
    width: int = 30,
    height: int = 8,
    seed: int = 42,
    output_dir: str = ".",
    difficulty: float = 0.5,
    include_unity_loader: bool = True,
) -> TilemapExportResult:
    """One-shot: generate a WFC level and export to Unity Tilemap JSON.

    Also optionally generates the Unity C# loader script.
    """
    physics = PhysicsConstraint.mario_default()
    wfc = ConstraintAwareWFC(
        physics=physics, seed=seed, difficulty_target=difficulty
    )
    wfc.learn()
    level_str = wfc.generate(width=width, height=height)

    exporter = WFCTilemapExporter(physics=physics)
    result = exporter.export_tilemap_json(level_str, output_dir=output_dir)

    if include_unity_loader:
        loader_path = generate_wfc_tilemap_loader(output_dir)
        # Attach loader path to result metadata
        if result.metadata:
            result.metadata.generator += f" | Unity loader: {loader_path}"

    return result
