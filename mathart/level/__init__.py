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
from .pdg import ProceduralDependencyGraph, PDGNode, PDGError
from .scene_description import UniversalSceneDescription, SceneLayer, ScenePrim

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
    "UniversalSceneDescription",
    "SceneLayer",
    "ScenePrim",
]
