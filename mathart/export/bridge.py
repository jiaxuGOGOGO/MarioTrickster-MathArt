"""
Asset export bridge for MarioTrickster Unity project.

Enforces all constraints from TA_AssetValidator and AI_SpriteSlicer:
  - PPU = 32
  - Filter = Point (no interpolation)
  - Alpha transparency preserved
  - Pivot: Bottom Center for characters, Center for VFX/terrain
  - Naming: {category}_{name}_{variant}_v{version}.png
  - Directory: Assets/Art/{Style}/{Category}/

Also generates metadata JSON for each exported asset, compatible with
LevelThemeProfile slot system and LevelSpecBridge-derived asset constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import json

from PIL import Image

from ..animation.industrial_renderer import IndustrialRenderAuxiliaryResult
from ..level.spec_bridge import (
    AssetCategory as LevelAssetCategory,
    AssetSpec,
    LevelSpecBridge,
    RenderMode,
    SpriteSpec,
)


# Unity constraints (from TA_AssetValidator.cs)
STANDARD_PPU = 32
TILE_SIZE = 32  # Base tile size in pixels
FILTER_MODE = "Point"


@dataclass
class ExportConfig:
    """Configuration for asset export."""

    output_dir: str = "./output"
    style_name: str = "Style_MathArt"
    version: int = 1
    ppu: int = STANDARD_PPU
    tile_size: int = TILE_SIZE

    # LevelThemeProfile element keys
    ELEMENT_KEYS = [
        "SpikeTrap",
        "FireTrap",
        "PendulumTrap",
        "BouncingEnemy",
        "BouncyPlatform",
        "CollapsingPlatform",
        "OneWayPlatform",
        "MovingPlatform",
        "HiddenPassage",
        "FakeWall",
        "GoalZone",
        "Collectible",
        "SimpleEnemy",
        "SawBlade",
        "FlyingEnemy",
        "ConveyorBelt",
        "Checkpoint",
        "BreakableBlock",
    ]


AssetCategory = Literal[
    "Characters",
    "Enemies",
    "Environment",
    "Hazards",
    "VFX",
    "UI",
]

PivotType = Literal["bottom_center", "center"]

# Category → Pivot mapping (from AI_SpriteSlicer)
CATEGORY_PIVOT: dict[AssetCategory, PivotType] = {
    "Characters": "bottom_center",
    "Enemies": "bottom_center",
    "Environment": "center",
    "Hazards": "center",
    "VFX": "center",
    "UI": "center",
}

LEVEL_TO_EXPORT_CATEGORY: dict[LevelAssetCategory, AssetCategory] = {
    LevelAssetCategory.PLAYER: "Characters",
    LevelAssetCategory.ENEMY: "Enemies",
    LevelAssetCategory.TILE: "Environment",
    LevelAssetCategory.ITEM: "Environment",
    LevelAssetCategory.BACKGROUND: "Environment",
    LevelAssetCategory.EFFECT: "VFX",
    LevelAssetCategory.UI: "UI",
}

DEFAULT_LEVEL_ELEMENT_KEYS: dict[LevelAssetCategory, str | None] = {
    LevelAssetCategory.PLAYER: None,
    LevelAssetCategory.ENEMY: "SimpleEnemy",
    LevelAssetCategory.TILE: None,
    LevelAssetCategory.ITEM: "Collectible",
    LevelAssetCategory.BACKGROUND: None,
    LevelAssetCategory.EFFECT: None,
    LevelAssetCategory.UI: None,
}

TAG_ELEMENT_HINTS: tuple[tuple[str, str], ...] = (
    ("spike", "SpikeTrap"),
    ("fire", "FireTrap"),
    ("flame", "FireTrap"),
    ("pendulum", "PendulumTrap"),
    ("bounce", "BouncingEnemy"),
    ("platform", "MovingPlatform"),
    ("hidden", "HiddenPassage"),
    ("fake", "FakeWall"),
    ("goal", "GoalZone"),
    ("collect", "Collectible"),
    ("coin", "Collectible"),
    ("enemy", "SimpleEnemy"),
    ("saw", "SawBlade"),
    ("flying", "FlyingEnemy"),
    ("conveyor", "ConveyorBelt"),
    ("checkpoint", "Checkpoint"),
    ("break", "BreakableBlock"),
)


@dataclass
class AssetMetadata:
    """Metadata for an exported asset."""

    name: str
    category: AssetCategory
    element_key: str | None = None  # Maps to LevelThemeProfile slot
    pivot: PivotType = "center"
    ppu: int = STANDARD_PPU
    frame_count: int = 1
    frame_width: int = TILE_SIZE
    frame_height: int = TILE_SIZE
    is_animated: bool = False
    palette_name: str | None = None
    version: int = 1
    level_id: str | None = None
    render_mode: str | None = None
    source_sprite_name: str | None = None
    tags: list[str] = field(default_factory=list)
    validation: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "element_key": self.element_key,
            "pivot": self.pivot,
            "pivot_xy": [0.5, 0.0] if self.pivot == "bottom_center" else [0.5, 0.5],
            "ppu": self.ppu,
            "frame_count": self.frame_count,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "is_animated": self.is_animated,
            "filter_mode": FILTER_MODE,
            "palette_name": self.palette_name,
            "version": self.version,
            "level_id": self.level_id,
            "render_mode": self.render_mode,
            "source_sprite_name": self.source_sprite_name,
            "tags": self.tags,
            "validation": self.validation,
        }


@dataclass
class LevelExportTarget:
    """Resolved export information derived from a LevelSpecBridge sprite spec."""

    export_name: str
    export_category: AssetCategory
    element_key: str | None
    sprite_spec: SpriteSpec
    level_id: str
    render_mode: str


class AssetExporter:
    """Export generated assets to Unity-compatible format.

    Handles:
    1. Image validation (size, transparency, pixel-perfect)
    2. Naming convention enforcement
    3. Directory structure creation
    4. Metadata generation
    5. Manifest for batch import
    6. Optional validation against LevelSpecBridge-derived AssetSpec
    """

    def __init__(self, config: ExportConfig | None = None):
        self.config = config or ExportConfig()
        self.manifest: list[dict] = []
        self.level_bridge = LevelSpecBridge()

    def export_sprite(
        self,
        image: Image.Image,
        name: str,
        category: AssetCategory,
        element_key: str | None = None,
        variant: str = "a",
        *,
        level_id: str | None = None,
        render_mode: str | None = None,
        source_sprite_name: str | None = None,
        tags: list[str] | None = None,
        validation: dict[str, object] | None = None,
    ) -> Path:
        """Export a single sprite with validation."""
        return self._export_image(
            image=image,
            name=name,
            category=category,
            variant=variant,
            frame_count=1,
            element_key=element_key,
            level_id=level_id,
            render_mode=render_mode,
            source_sprite_name=source_sprite_name,
            tags=tags or [],
            validation=validation or {},
        )

    def export_spritesheet(
        self,
        image: Image.Image,
        name: str,
        category: AssetCategory,
        frame_count: int,
        element_key: str | None = None,
        variant: str = "a",
        *,
        level_id: str | None = None,
        render_mode: str | None = None,
        source_sprite_name: str | None = None,
        tags: list[str] | None = None,
        validation: dict[str, object] | None = None,
    ) -> Path:
        """Export an animated sprite sheet with metadata."""
        return self._export_image(
            image=image,
            name=name,
            category=category,
            variant=variant,
            frame_count=frame_count,
            element_key=element_key,
            level_id=level_id,
            render_mode=render_mode,
            source_sprite_name=source_sprite_name,
            tags=tags or [],
            validation=validation or {},
        )

    def export_industrial_bundle(
        self,
        bundle: IndustrialRenderAuxiliaryResult,
        name: str,
        category: AssetCategory,
        element_key: str | None = None,
        variant: str = "a",
        *,
        level_id: str | None = None,
        render_mode: str | None = "industrial_2p5d",
        source_sprite_name: str | None = None,
        tags: list[str] | None = None,
        validation: dict[str, object] | None = None,
    ) -> Path:
        """Export an industrial sprite plus its engine-ready material bundle."""
        albedo_path = self.export_sprite(
            bundle.albedo_image,
            name=name,
            category=category,
            element_key=element_key,
            variant=variant,
            level_id=level_id,
            render_mode=render_mode,
            source_sprite_name=source_sprite_name,
            tags=tags or [],
            validation=validation or {},
        )
        aux_images = {
            "normal": bundle.normal_map_image,
            "depth": bundle.depth_map_image,
            "thickness": bundle.thickness_map_image,
            "roughness": bundle.roughness_map_image,
            "mask": bundle.mask_image,
        }
        aux_paths: dict[str, str] = {}
        for channel, image in aux_images.items():
            channel_path = albedo_path.with_name(f"{albedo_path.stem}_{channel}.png")
            image.save(str(channel_path))
            aux_paths[channel] = str(channel_path.relative_to(self.config.output_dir))

        meta_path = albedo_path.with_suffix(".meta.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        material_bundle = {
            "workflow": "industrial_2p5d",
            "channels": aux_paths,
            "engine_targets": bundle.metadata.get("engine_targets", ["Unity URP 2D", "Godot 4"]),
            "channel_semantics": bundle.metadata.get("engine_channels", {}),
            "bundle_metadata": bundle.metadata,
        }
        meta["material_bundle"] = material_bundle
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        if self.manifest:
            self.manifest[-1]["material_bundle"] = material_bundle
        return albedo_path

    def export_from_asset_spec(
        self,
        image: Image.Image,
        asset_spec: AssetSpec,
        sprite_name: str,
        *,
        name: str | None = None,
        variant: str = "a",
        element_key: str | None = None,
    ) -> Path:
        """Export a single-frame asset using LevelSpecBridge constraints."""
        target = self._resolve_level_target(asset_spec, sprite_name, name, element_key)
        validation = self.validate_level_asset(image, asset_spec, sprite_name)
        if validation["violations"]:
            raise ValueError(self._format_validation_error(sprite_name, validation["violations"]))
        return self.export_sprite(
            image,
            name=target.export_name,
            category=target.export_category,
            element_key=target.element_key,
            variant=variant,
            level_id=target.level_id,
            render_mode=target.render_mode,
            source_sprite_name=sprite_name,
            tags=list(target.sprite_spec.tags),
            validation=validation,
        )

    def export_sheet_from_asset_spec(
        self,
        image: Image.Image,
        asset_spec: AssetSpec,
        sprite_name: str,
        *,
        name: str | None = None,
        variant: str = "a",
        element_key: str | None = None,
    ) -> Path:
        """Export an animated spritesheet using LevelSpecBridge constraints."""
        target = self._resolve_level_target(asset_spec, sprite_name, name, element_key)
        validation = self.validate_level_asset(image, asset_spec, sprite_name, frame_count=target.sprite_spec.frame_count)
        if validation["violations"]:
            raise ValueError(self._format_validation_error(sprite_name, validation["violations"]))
        return self.export_spritesheet(
            image,
            name=target.export_name,
            category=target.export_category,
            frame_count=target.sprite_spec.frame_count,
            element_key=target.element_key,
            variant=variant,
            level_id=target.level_id,
            render_mode=target.render_mode,
            source_sprite_name=sprite_name,
            tags=list(target.sprite_spec.tags),
            validation=validation,
        )

    def validate_level_asset(
        self,
        image: Image.Image,
        asset_spec: AssetSpec,
        sprite_name: str,
        frame_count: int | None = None,
    ) -> dict[str, object]:
        """Validate an image against a LevelSpecBridge-derived AssetSpec."""
        self._validate_image(image)
        sprite = asset_spec.get_sprite(sprite_name)
        if sprite is None:
            raise ValueError(f"Unknown sprite name: {sprite_name}")

        actual_frames = frame_count if frame_count is not None else 1
        if actual_frames < 1:
            raise ValueError("frame_count must be >= 1")
        if image.width % actual_frames != 0:
            raise ValueError(
                f"Image width {image.width} is not divisible by frame count {actual_frames}"
            )

        frame_width = image.width // actual_frames
        frame_height = image.height
        color_count = self._count_colors(image)
        violations = list(
            self.level_bridge.validate_asset(
                asset_spec,
                sprite_name,
                frame_width,
                frame_height,
                color_count,
            )
        )

        if actual_frames != sprite.frame_count:
            violations.append(
                f"Frame count mismatch: got {actual_frames}, expected {sprite.frame_count}"
            )
        if actual_frames > 1 and image.width != sprite.sheet_width:
            violations.append(
                f"Spritesheet width mismatch: got {image.width}px, expected {sprite.sheet_width}px"
            )
        if sprite.category == LevelAssetCategory.TILE:
            tile_size_min = int(asset_spec.constraints.get("tile_size", (sprite.width, sprite.width))[0])
            if frame_width < tile_size_min or frame_height < tile_size_min:
                violations.append(
                    f"Tile asset below minimum tile constraint: {frame_width}x{frame_height}px < {tile_size_min}px"
                )

        return {
            "valid": not violations,
            "violations": violations,
            "expected": {
                "width": sprite.width,
                "height": sprite.height,
                "frame_count": sprite.frame_count,
                "palette_size": sprite.palette_size,
                "category": sprite.category.value,
            },
            "actual": {
                "width": frame_width,
                "height": frame_height,
                "frame_count": actual_frames,
                "color_count": color_count,
            },
        }

    def save_manifest(self) -> Path:
        """Save the export manifest."""
        out_path = Path(self.config.output_dir) / "manifest.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "style": self.config.style_name,
                    "version": self.config.version,
                    "ppu": self.config.ppu,
                    "assets": self.manifest,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return out_path

    def _export_image(
        self,
        *,
        image: Image.Image,
        name: str,
        category: AssetCategory,
        variant: str,
        frame_count: int,
        element_key: str | None,
        level_id: str | None,
        render_mode: str | None,
        source_sprite_name: str | None,
        tags: list[str],
        validation: dict[str, object],
    ) -> Path:
        self._validate_image(image)
        if frame_count < 1:
            raise ValueError("frame_count must be >= 1")
        if image.width % frame_count != 0:
            raise ValueError(
                f"Image width {image.width} is not divisible by frame count {frame_count}"
            )

        pivot = CATEGORY_PIVOT.get(category, "center")
        suffix = "_sheet" if frame_count > 1 else ""
        filename = f"{name}_{variant}{suffix}_v{self.config.version:02d}.png"
        rel_dir = Path(self.config.style_name) / category
        out_dir = Path(self.config.output_dir) / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        image.save(str(out_path))

        meta = AssetMetadata(
            name=name,
            category=category,
            element_key=element_key,
            pivot=pivot,
            ppu=self.config.ppu,
            frame_count=frame_count,
            frame_width=image.width // frame_count,
            frame_height=image.height,
            is_animated=frame_count > 1,
            version=self.config.version,
            level_id=level_id,
            render_mode=render_mode,
            source_sprite_name=source_sprite_name,
            tags=tags,
            validation=validation,
        )
        meta_path = out_path.with_suffix(".meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, indent=2, ensure_ascii=False)

        self.manifest.append(
            {
                "file": str(out_path.relative_to(self.config.output_dir)),
                "meta": str(meta_path.relative_to(self.config.output_dir)),
                **meta.to_dict(),
            }
        )
        return out_path

    def _resolve_level_target(
        self,
        asset_spec: AssetSpec,
        sprite_name: str,
        name: str | None,
        element_key: str | None,
    ) -> LevelExportTarget:
        sprite = asset_spec.get_sprite(sprite_name)
        if sprite is None:
            raise ValueError(f"Unknown sprite name: {sprite_name}")
        export_category = LEVEL_TO_EXPORT_CATEGORY[sprite.category]
        resolved_element = element_key or self._infer_element_key(sprite)
        return LevelExportTarget(
            export_name=name or sprite.name,
            export_category=export_category,
            element_key=resolved_element,
            sprite_spec=sprite,
            level_id=asset_spec.level_id,
            render_mode=asset_spec.render_mode.value,
        )

    def _infer_element_key(self, sprite: SpriteSpec) -> str | None:
        default_key = DEFAULT_LEVEL_ELEMENT_KEYS.get(sprite.category)
        if default_key is not None:
            return default_key
        haystack = " ".join([sprite.name, *sprite.tags]).lower()
        for needle, element_key in TAG_ELEMENT_HINTS:
            if needle in haystack:
                return element_key
        return None

    def _validate_image(self, image: Image.Image) -> None:
        """Validate image meets Unity constraints."""
        if image.mode != "RGBA":
            raise ValueError(f"Image must be RGBA, got {image.mode}")
        if image.width > 2048 or image.height > 2048:
            raise ValueError(f"Image too large: {image.width}x{image.height}")

    def _count_colors(self, image: Image.Image) -> int:
        rgba = image.convert("RGBA")
        colors = rgba.getcolors(maxcolors=rgba.width * rgba.height)
        if colors is not None:
            return len(colors)
        return len(set(rgba.getdata()))

    def _format_validation_error(self, sprite_name: str, violations: list[str]) -> str:
        return f"Level spec validation failed for '{sprite_name}': " + "; ".join(violations)
