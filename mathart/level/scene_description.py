"""Unified scene description utilities for procedural level generation.

This module provides a lightweight, USD-inspired scene container that turns
ASCII/WFC level outputs into a structured intermediate representation.
The goal is not to fully re-implement Pixar USD, but to borrow the core idea:
a single scene graph representation that downstream systems can consume without
knowing where the data originally came from.

In this repository, the scene description serves as the common artery between:
  - WFC / discrete gameplay topology
  - shader planning / render intent
  - export / bundle and manifest generation
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .spec_bridge import LevelSpec, RenderMode
from .templates import (
    AIR_CHAR,
    ELEMENT_MAP,
    ENEMY_CHARS,
    HAZARD_CHARS,
    PLATFORM_CHARS,
    SOLID_CHARS,
    SPAWN_CHARS,
)


CATEGORY_BY_TILE: dict[str, str] = {}
for _tile in SOLID_CHARS:
    CATEGORY_BY_TILE[_tile] = "solid"
for _tile in PLATFORM_CHARS:
    CATEGORY_BY_TILE[_tile] = "platform"
for _tile in HAZARD_CHARS:
    CATEGORY_BY_TILE[_tile] = "hazard"
for _tile in ENEMY_CHARS:
    CATEGORY_BY_TILE[_tile] = "enemy"
for _tile in SPAWN_CHARS:
    CATEGORY_BY_TILE[_tile] = "spawn"
CATEGORY_BY_TILE.setdefault("G", "goal")
CATEGORY_BY_TILE.setdefault("C", "collectible")
CATEGORY_BY_TILE.setdefault(AIR_CHAR, "air")


@dataclass
class ScenePrim:
    """A single tile- or object-level primitive in the scene graph."""

    path: str
    prim_type: str
    tile_char: str
    tile_name: str
    x: int
    y: int
    width: int
    height: int
    tags: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "prim_type": self.prim_type,
            "tile_char": self.tile_char,
            "tile_name": self.tile_name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "tags": list(self.tags),
            "attributes": dict(self.attributes),
        }


@dataclass
class SceneLayer:
    """A logical layer in the scene graph."""

    name: str
    purpose: str
    prims: list[ScenePrim] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "prim_count": len(self.prims),
            "prims": [prim.to_dict() for prim in self.prims],
        }


@dataclass
class UniversalSceneDescription:
    """USD-inspired scene description for 2D platformer levels."""

    scene_id: str
    level_spec: LevelSpec
    ascii_layout: str
    layers: list[SceneLayer]
    metrics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = "usd_like_scene_v1"

    @classmethod
    def from_ascii_level(
        cls,
        ascii_layout: str,
        level_spec: LevelSpec,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "UniversalSceneDescription":
        rows = [list(line) for line in ascii_layout.strip("\n").splitlines() if line]
        if not rows:
            raise ValueError("ascii_layout must contain at least one non-empty row")

        tile_w = int(level_spec.tile_width)
        tile_h = int(level_spec.tile_height)

        topology_prims: list[ScenePrim] = []
        gameplay_prims: list[ScenePrim] = []
        category_counts: dict[str, int] = {
            "air": 0,
            "solid": 0,
            "platform": 0,
            "hazard": 0,
            "enemy": 0,
            "spawn": 0,
            "goal": 0,
            "collectible": 0,
            "other": 0,
        }

        for y, row in enumerate(rows):
            for x, tile in enumerate(row):
                category = CATEGORY_BY_TILE.get(tile, "other")
                category_counts[category] = category_counts.get(category, 0) + 1
                tile_name = ELEMENT_MAP.get(tile, "Unknown")
                prim = ScenePrim(
                    path=f"/Level/Topology/r{y}_c{x}_{ord(tile)}",
                    prim_type=category,
                    tile_char=tile,
                    tile_name=tile_name,
                    x=x * tile_w,
                    y=y * tile_h,
                    width=tile_w,
                    height=tile_h,
                    tags=[category, tile_name.lower().replace(" ", "_")],
                    attributes={
                        "grid_x": x,
                        "grid_y": y,
                        "theme": level_spec.theme.value,
                        "render_mode": level_spec.render_mode.value,
                    },
                )
                topology_prims.append(prim)
                if category not in {"air", "solid", "platform"}:
                    gameplay_prims.append(prim)

        total_cells = max(1, sum(len(row) for row in rows))
        hazard_density = category_counts.get("hazard", 0) / total_cells
        traversable_ratio = 1.0 - (
            (category_counts.get("solid", 0) + category_counts.get("hazard", 0)) / total_cells
        )
        vertical_activity = 0
        for x in range(max(len(r) for r in rows)):
            column = [rows[y][x] for y in range(len(rows)) if x < len(rows[y])]
            categories = {CATEGORY_BY_TILE.get(t, "other") for t in column if t != AIR_CHAR}
            if len(categories) > 1:
                vertical_activity += 1

        metrics = {
            "grid_cols": max(len(r) for r in rows),
            "grid_rows": len(rows),
            "tile_size": [tile_w, tile_h],
            "render_mode": level_spec.render_mode.value,
            "theme": level_spec.theme.value,
            "counts": category_counts,
            "hazard_density": round(hazard_density, 4),
            "traversable_ratio": round(traversable_ratio, 4),
            "vertical_activity_ratio": round(vertical_activity / max(1, max(len(r) for r in rows)), 4),
        }

        layers = [
            SceneLayer(name="topology", purpose="All grid cells from WFC output", prims=topology_prims),
            SceneLayer(name="gameplay", purpose="Interactive gameplay-relevant objects", prims=gameplay_prims),
        ]

        return cls(
            scene_id=level_spec.level_id,
            level_spec=level_spec,
            ascii_layout=ascii_layout,
            layers=layers,
            metrics=metrics,
            metadata=dict(metadata or {}),
        )

    def derive_shader_recipe(self, shader_goal: str = "auto") -> dict[str, Any]:
        """Infer a practical shader recipe from scene structure.

        This is the glue that turns discrete topology into render intent.
        """
        counts = self.metrics.get("counts", {})
        render_mode = self.level_spec.render_mode

        if render_mode in {RenderMode.PSEUDO_3D, RenderMode.ISOMETRIC}:
            base_shader = "pseudo_3d_depth"
            preset_name = "pseudo_3d_isometric"
        elif self.metrics.get("hazard_density", 0.0) >= 0.08 or counts.get("enemy", 0) > 0:
            base_shader = "sprite_lit"
            preset_name = "pixel_art_lit"
        else:
            base_shader = "sprite_lit"
            preset_name = "pixel_art_clean"

        if shader_goal == "outline":
            base_shader = "outline"
            preset_name = "crisp_outline"
        elif shader_goal == "palette":
            base_shader = "palette_swap"
            preset_name = "palette_8color"
        elif shader_goal == "pseudo_3d":
            base_shader = "pseudo_3d_depth"
            preset_name = "pseudo_3d_isometric"

        overlays: list[dict[str, str]] = []
        if base_shader != "outline" and counts.get("solid", 0) > 0:
            overlays.append({"shader_type": "outline", "preset_name": "crisp_outline"})
        if self.level_spec.palette_size <= 8 and base_shader != "palette_swap":
            overlays.append({"shader_type": "palette_swap", "preset_name": "palette_8color"})

        return {
            "shader_type": base_shader,
            "preset_name": preset_name,
            "overlays": overlays,
            "scene_conditioning": {
                "hazard_density": self.metrics.get("hazard_density", 0.0),
                "vertical_activity_ratio": self.metrics.get("vertical_activity_ratio", 0.0),
                "render_mode": self.level_spec.render_mode.value,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_version,
            "scene_id": self.scene_id,
            "level_spec": self.level_spec.to_dict(),
            "ascii_layout": self.ascii_layout,
            "layers": [layer.to_dict() for layer in self.layers],
            "metrics": dict(self.metrics),
            "metadata": dict(self.metadata),
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
