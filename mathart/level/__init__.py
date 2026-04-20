"""WFC-based procedural level generation for MarioTrickster.

This module implements Wave Function Collapse (WFC) to automatically generate
ASCII-format level layouts that are directly compatible with the main project's
Level Studio system. It learns adjacency rules from existing level templates
and produces new, structurally valid level variations.
"""

from .wfc import WFCGenerator, AdjacencyRules
from .templates import CLASSIC_FRAGMENTS, ELEMENT_MAP
from .spec_bridge import (
    LevelSpecBridge,
    LevelSpec,
    AssetSpec,
    SpriteSpec,
    LevelTheme,
    AssetCategory,
    RenderMode,
)
from .pdg import (
    ProceduralDependencyGraph,
    PDGNode,
    PDGError,
    WorkItem,
    PDGFanOutItem,
    PDGFanOutResult,
)
from .scene_description import UniversalSceneDescription, SceneLayer, ScenePrim
# SESSION-057: Constraint-Aware WFC (P2 — Cross-Dimensional Spawning)
from .constraint_wfc import (
    ConstraintAwareWFC,
    PhysicsConstraint,
    ReachabilityValidator,
    TilePlatformExtractor,
)
# SESSION-062: WFC Tilemap Exporter with Dual Grid Mapping
from .wfc_tilemap_exporter import (
    WFCTilemapExporter,
    DualGridMapper,
    DualGridCell,
    TilemapExportResult,
    TilemapMetadata,
    generate_and_export_tilemap,
    generate_wfc_tilemap_loader,
)

__all__ = [
    "WFCGenerator",
    "AdjacencyRules",
    "CLASSIC_FRAGMENTS",
    "ELEMENT_MAP",
    "LevelSpecBridge",
    "LevelSpec",
    "AssetSpec",
    "SpriteSpec",
    "LevelTheme",
    "AssetCategory",
    "RenderMode",
    "ProceduralDependencyGraph",
    "PDGNode",
    "PDGError",
    "WorkItem",
    "PDGFanOutItem",
    "PDGFanOutResult",
    "UniversalSceneDescription",
    "SceneLayer",
    "ScenePrim",
    # SESSION-057: Constraint-Aware WFC
    "ConstraintAwareWFC",
    "PhysicsConstraint",
    "ReachabilityValidator",
    "TilePlatformExtractor",
    # SESSION-062: WFC Tilemap Exporter with Dual Grid
    "WFCTilemapExporter",
    "DualGridMapper",
    "DualGridCell",
    "TilemapExportResult",
    "TilemapMetadata",
    "generate_and_export_tilemap",
    "generate_wfc_tilemap_loader",
]
