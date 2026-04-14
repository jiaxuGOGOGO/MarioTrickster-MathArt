"""LevelSpecBridge — connect level design requirements to art asset generation.

This module translates level design specifications into concrete art asset
requirements:
  - Pixel dimensions and sprite sizes
  - Color palette constraints (per-level theme)
  - Animation frame counts and timing
  - Tile grid specifications
  - Character scale relative to level grid

The bridge ensures that generated assets are always compatible with the
level they will be used in, preventing scale mismatches and palette conflicts.

Typical flow:
  1. Level designer defines a LevelSpec (grid size, theme, palette)
  2. LevelSpecBridge.to_asset_spec() converts it to AssetSpec
  3. AssetSpec is fed into the generation pipeline as hard constraints
  4. Generated assets are validated against AssetSpec before export

Supports future pseudo-3D levels via the Pseudo3DSpec extension.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Enums ──────────────────────────────────────────────────────────────────────

class LevelTheme(str, Enum):
    GRASSLAND  = "grassland"
    UNDERGROUND = "underground"
    CASTLE     = "castle"
    WATER      = "water"
    SKY        = "sky"
    DESERT     = "desert"
    SNOW       = "snow"
    LAVA       = "lava"
    FOREST     = "forest"
    CUSTOM     = "custom"


class AssetCategory(str, Enum):
    PLAYER     = "player"
    ENEMY      = "enemy"
    TILE       = "tile"
    ITEM       = "item"
    BACKGROUND = "background"
    EFFECT     = "effect"
    UI         = "ui"


class RenderMode(str, Enum):
    FLAT_2D    = "flat_2d"
    PSEUDO_3D  = "pseudo_3d"
    ISOMETRIC  = "isometric"


# ── Spec data structures ───────────────────────────────────────────────────────

@dataclass
class SpriteSpec:
    """Specification for a single sprite asset."""
    name:          str
    category:      AssetCategory
    width:         int              # Pixels
    height:        int              # Pixels
    frame_count:   int = 1          # For animations
    fps:           float = 12.0     # Animation frames per second
    palette_size:  int = 16         # Max colors
    has_outline:   bool = True
    scale:         int = 1          # Pixel art scale (1=native, 2=2x)
    tags:          list[str] = field(default_factory=list)

    @property
    def sheet_width(self) -> int:
        """Width of the spritesheet for this animation."""
        return self.width * self.frame_count

    @property
    def sheet_height(self) -> int:
        return self.height

    def to_export_config(self) -> dict:
        """Convert to ExportConfig-compatible dict."""
        return {
            "width":        self.width,
            "height":       self.height,
            "scale":        self.scale,
            "palette_size": self.palette_size,
        }


@dataclass
class LevelSpec:
    """Complete specification for a game level."""
    level_id:      str
    theme:         LevelTheme
    render_mode:   RenderMode = RenderMode.FLAT_2D

    # Grid
    tile_width:    int = 16         # Pixels per tile
    tile_height:   int = 16
    grid_cols:     int = 20         # Level width in tiles
    grid_rows:     int = 15         # Level height in tiles

    # Visual
    palette_size:  int = 16         # Global palette constraint
    bg_color:      tuple[int, int, int] = (92, 148, 252)  # Sky blue default
    theme_colors:  list[tuple[int, int, int]] = field(default_factory=list)

    # Assets required
    required_assets: list[str] = field(default_factory=list)

    # Pseudo-3D settings (for future use)
    iso_angle:     float = 30.0     # Isometric angle in degrees
    parallax_layers: int = 3

    # Custom overrides
    custom_specs:  dict[str, dict] = field(default_factory=dict)

    @property
    def screen_width(self) -> int:
        return self.tile_width * self.grid_cols

    @property
    def screen_height(self) -> int:
        return self.tile_height * self.grid_rows

    def to_dict(self) -> dict:
        d = asdict(self)
        d["theme"] = self.theme.value
        d["render_mode"] = self.render_mode.value
        return d


@dataclass
class AssetSpec:
    """Concrete asset generation specification derived from a LevelSpec."""
    level_id:      str
    theme:         LevelTheme
    render_mode:   RenderMode
    sprites:       list[SpriteSpec]
    global_palette_size: int
    global_palette: list[tuple[int, int, int]]  # Theme colors
    constraints:   dict[str, tuple[float, float]]  # Param constraints

    def get_sprite(self, name: str) -> Optional[SpriteSpec]:
        for s in self.sprites:
            if s.name == name:
                return s
        return None

    def get_by_category(self, category: AssetCategory) -> list[SpriteSpec]:
        return [s for s in self.sprites if s.category == category]

    def to_dict(self) -> dict:
        return {
            "level_id":     self.level_id,
            "theme":        self.theme.value,
            "render_mode":  self.render_mode.value,
            "sprite_count": len(self.sprites),
            "sprites":      [asdict(s) for s in self.sprites],
            "palette_size": self.global_palette_size,
            "constraints":  {k: list(v) for k, v in self.constraints.items()},
        }

    def summary(self) -> str:
        lines = [
            f"AssetSpec for level '{self.level_id}' ({self.theme.value})",
            f"  Render mode: {self.render_mode.value}",
            f"  Sprites: {len(self.sprites)}",
            f"  Palette: {self.global_palette_size} colors",
        ]
        for cat in AssetCategory:
            sprites = self.get_by_category(cat)
            if sprites:
                lines.append(f"  {cat.value}: {len(sprites)} sprites")
                for s in sprites[:3]:
                    lines.append(f"    - {s.name} {s.width}x{s.height}px "
                                 f"({s.frame_count} frames)")
        return "\n".join(lines)


# ── Bridge ─────────────────────────────────────────────────────────────────────

class LevelSpecBridge:
    """Converts level specifications into art asset generation requirements.

    Parameters
    ----------
    project_root : Path, optional
        Root directory for loading/saving specs.
    """

    # Default sprite sizes per category (tile_size multipliers)
    _SIZE_MULTIPLIERS: dict[AssetCategory, tuple[float, float]] = {
        AssetCategory.PLAYER:     (1.0, 2.0),   # 1 tile wide, 2 tiles tall
        AssetCategory.ENEMY:      (1.0, 1.5),
        AssetCategory.TILE:       (1.0, 1.0),
        AssetCategory.ITEM:       (0.75, 0.75),
        AssetCategory.BACKGROUND: (20.0, 15.0), # Full screen
        AssetCategory.EFFECT:     (1.0, 1.0),
        AssetCategory.UI:         (1.0, 1.0),
    }

    # Default frame counts per category
    _DEFAULT_FRAMES: dict[AssetCategory, int] = {
        AssetCategory.PLAYER:     8,
        AssetCategory.ENEMY:      4,
        AssetCategory.TILE:       1,
        AssetCategory.ITEM:       4,
        AssetCategory.BACKGROUND: 1,
        AssetCategory.EFFECT:     8,
        AssetCategory.UI:         1,
    }

    # Theme color palettes (base colors, expanded by the generator)
    _THEME_PALETTES: dict[LevelTheme, list[tuple[int, int, int]]] = {
        LevelTheme.GRASSLAND:   [(92, 148, 252), (0, 168, 68), (172, 124, 0), (255, 255, 255)],
        LevelTheme.UNDERGROUND: [(0, 0, 0), (68, 68, 68), (136, 100, 60), (200, 160, 100)],
        LevelTheme.CASTLE:      [(80, 80, 80), (160, 160, 160), (200, 0, 0), (255, 200, 0)],
        LevelTheme.WATER:       [(0, 80, 160), (0, 160, 220), (255, 255, 255), (0, 200, 100)],
        LevelTheme.SKY:         [(135, 206, 235), (255, 255, 255), (255, 200, 0), (200, 100, 0)],
        LevelTheme.DESERT:      [(240, 200, 100), (200, 160, 60), (255, 160, 0), (80, 40, 0)],
        LevelTheme.SNOW:        [(200, 220, 255), (255, 255, 255), (100, 150, 200), (0, 0, 80)],
        LevelTheme.LAVA:        [(200, 0, 0), (255, 100, 0), (255, 200, 0), (0, 0, 0)],
        LevelTheme.FOREST:      [(0, 80, 0), (0, 140, 0), (100, 60, 0), (200, 180, 100)],
        LevelTheme.CUSTOM:      [],
    }

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()

    def to_asset_spec(
        self,
        level_spec: LevelSpec,
        asset_categories: Optional[list[AssetCategory]] = None,
    ) -> AssetSpec:
        """Convert a LevelSpec to a concrete AssetSpec.

        Parameters
        ----------
        level_spec : LevelSpec
            The level design specification.
        asset_categories : list[AssetCategory], optional
            Which asset categories to generate specs for.
            Default: all categories.
        """
        if asset_categories is None:
            asset_categories = list(AssetCategory)

        sprites: list[SpriteSpec] = []
        tw = level_spec.tile_width
        th = level_spec.tile_height

        for category in asset_categories:
            # Check for custom override
            cat_key = category.value
            if cat_key in level_spec.custom_specs:
                override = level_spec.custom_specs[cat_key]
                sprite = SpriteSpec(
                    name=override.get("name", cat_key),
                    category=category,
                    width=override.get("width", tw),
                    height=override.get("height", th),
                    frame_count=override.get("frame_count", 1),
                    fps=override.get("fps", 12.0),
                    palette_size=override.get("palette_size", level_spec.palette_size),
                    has_outline=override.get("has_outline", True),
                    scale=override.get("scale", 1),
                    tags=override.get("tags", [category.value]),
                )
                sprites.append(sprite)
                continue

            # Default sizing
            wm, hm = self._SIZE_MULTIPLIERS.get(category, (1.0, 1.0))
            w = max(1, round(tw * wm))
            h = max(1, round(th * hm))

            # Skip background if not in required assets
            if category == AssetCategory.BACKGROUND:
                if "background" not in level_spec.required_assets:
                    continue
                w = level_spec.screen_width
                h = level_spec.screen_height

            frame_count = self._DEFAULT_FRAMES.get(category, 1)

            sprite = SpriteSpec(
                name=f"{level_spec.level_id}_{category.value}",
                category=category,
                width=w,
                height=h,
                frame_count=frame_count,
                fps=12.0,
                palette_size=level_spec.palette_size,
                has_outline=category in (AssetCategory.PLAYER, AssetCategory.ENEMY),
                scale=1,
                tags=[category.value, level_spec.theme.value],
            )
            sprites.append(sprite)

        # Build global palette
        theme_palette = self._THEME_PALETTES.get(level_spec.theme, [])
        if level_spec.theme_colors:
            theme_palette = level_spec.theme_colors

        # Build parameter constraints
        constraints = self._build_constraints(level_spec)

        return AssetSpec(
            level_id=level_spec.level_id,
            theme=level_spec.theme,
            render_mode=level_spec.render_mode,
            sprites=sprites,
            global_palette_size=level_spec.palette_size,
            global_palette=theme_palette,
            constraints=constraints,
        )

    def validate_asset(
        self,
        asset_spec: AssetSpec,
        sprite_name: str,
        image_width: int,
        image_height: int,
        color_count: int,
    ) -> list[str]:
        """Validate a generated asset against its spec.

        Returns a list of violation strings (empty = valid).
        """
        violations = []
        sprite = asset_spec.get_sprite(sprite_name)
        if sprite is None:
            return [f"Unknown sprite name: {sprite_name}"]

        if image_width != sprite.width:
            violations.append(
                f"Width mismatch: got {image_width}px, expected {sprite.width}px"
            )
        if image_height != sprite.height:
            violations.append(
                f"Height mismatch: got {image_height}px, expected {sprite.height}px"
            )
        if color_count > sprite.palette_size:
            violations.append(
                f"Too many colors: {color_count} > {sprite.palette_size}"
            )

        return violations

    def save_spec(self, asset_spec: AssetSpec, filename: Optional[str] = None) -> Path:
        """Save an AssetSpec to a JSON file."""
        specs_dir = self.root / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        filename = filename or f"{asset_spec.level_id}_spec.json"
        path = specs_dir / filename
        path.write_text(
            json.dumps(asset_spec.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def load_spec(self, filepath: str | Path) -> AssetSpec:
        """Load an AssetSpec from a JSON file."""
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        sprites = [
            SpriteSpec(
                name=s["name"],
                category=AssetCategory(s["category"]),
                width=s["width"],
                height=s["height"],
                frame_count=s.get("frame_count", 1),
                fps=s.get("fps", 12.0),
                palette_size=s.get("palette_size", 16),
                has_outline=s.get("has_outline", True),
                scale=s.get("scale", 1),
                tags=s.get("tags", []),
            )
            for s in data.get("sprites", [])
        ]
        constraints = {k: tuple(v) for k, v in data.get("constraints", {}).items()}
        return AssetSpec(
            level_id=data["level_id"],
            theme=LevelTheme(data["theme"]),
            render_mode=RenderMode(data.get("render_mode", "flat_2d")),
            sprites=sprites,
            global_palette_size=data.get("palette_size", 16),
            global_palette=[],
            constraints=constraints,
        )

    def create_mario_style_spec(self, level_id: str = "world_1_1") -> LevelSpec:
        """Create a Mario-style level spec as a starting template."""
        return LevelSpec(
            level_id=level_id,
            theme=LevelTheme.GRASSLAND,
            render_mode=RenderMode.FLAT_2D,
            tile_width=16,
            tile_height=16,
            grid_cols=224,   # Classic Mario level width
            grid_rows=15,
            palette_size=16,
            bg_color=(92, 148, 252),
            required_assets=["player", "enemy", "tile", "item", "background"],
            custom_specs={
                "player": {
                    "name": "mario",
                    "width": 16,
                    "height": 32,
                    "frame_count": 8,
                    "fps": 12.0,
                    "palette_size": 4,
                    "has_outline": True,
                    "tags": ["player", "character", "mario"],
                },
                "enemy": {
                    "name": "goomba",
                    "width": 16,
                    "height": 16,
                    "frame_count": 2,
                    "fps": 8.0,
                    "palette_size": 4,
                    "has_outline": True,
                    "tags": ["enemy", "goomba"],
                },
            },
        )

    def _build_constraints(self, spec: LevelSpec) -> dict[str, tuple[float, float]]:
        """Build parameter constraints from level spec."""
        tw, th = spec.tile_width, spec.tile_height
        return {
            "tile_size":      (min(tw, th) * 0.8, max(tw, th) * 1.2),
            "palette_size":   (2.0, float(spec.palette_size)),
            "contrast":       (0.3, 0.9),
            "saturation":     (0.3, 0.9),
            "edge_density":   (0.05, 0.4),
            "fill_ratio":     (0.2, 0.9),
        }
