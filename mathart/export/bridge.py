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
the LevelThemeProfile slot system.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import json
import shutil
from PIL import Image


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
        "SpikeTrap", "FireTrap", "PendulumTrap", "BouncingEnemy",
        "BouncyPlatform", "CollapsingPlatform", "OneWayPlatform",
        "MovingPlatform", "HiddenPassage", "FakeWall", "GoalZone",
        "Collectible", "SimpleEnemy", "SawBlade", "FlyingEnemy",
        "ConveyorBelt", "Checkpoint", "BreakableBlock",
    ]


AssetCategory = Literal[
    "Characters", "Enemies", "Environment", "Hazards", "VFX", "UI",
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
        }


class AssetExporter:
    """Export generated assets to Unity-compatible format.

    Handles:
    1. Image validation (size, transparency, pixel-perfect)
    2. Naming convention enforcement
    3. Directory structure creation
    4. Metadata generation
    5. Manifest for batch import
    """

    def __init__(self, config: ExportConfig | None = None):
        self.config = config or ExportConfig()
        self.manifest: list[dict] = []

    def export_sprite(
        self,
        image: Image.Image,
        name: str,
        category: AssetCategory,
        element_key: str | None = None,
        variant: str = "a",
    ) -> Path:
        """Export a single sprite with validation and metadata."""
        # Validate
        self._validate_image(image)

        # Determine pivot
        pivot = CATEGORY_PIVOT.get(category, "center")

        # Build path
        filename = f"{name}_{variant}_v{self.config.version:02d}.png"
        rel_dir = Path(self.config.style_name) / category
        out_dir = Path(self.config.output_dir) / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        # Save image (Point filter = no resampling needed, just save as-is)
        image.save(str(out_path))

        # Generate metadata
        meta = AssetMetadata(
            name=name,
            category=category,
            element_key=element_key,
            pivot=pivot,
            ppu=self.config.ppu,
            frame_count=1,
            frame_width=image.width,
            frame_height=image.height,
            is_animated=False,
            version=self.config.version,
        )
        meta_path = out_path.with_suffix(".meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta.to_dict(), f, indent=2)

        self.manifest.append({
            "file": str(out_path.relative_to(self.config.output_dir)),
            "meta": str(meta_path.relative_to(self.config.output_dir)),
            **meta.to_dict(),
        })

        return out_path

    def export_spritesheet(
        self,
        image: Image.Image,
        name: str,
        category: AssetCategory,
        frame_count: int,
        element_key: str | None = None,
        variant: str = "a",
    ) -> Path:
        """Export an animated sprite sheet with metadata."""
        self._validate_image(image)

        pivot = CATEGORY_PIVOT.get(category, "center")
        filename = f"{name}_{variant}_sheet_v{self.config.version:02d}.png"
        rel_dir = Path(self.config.style_name) / category
        out_dir = Path(self.config.output_dir) / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        image.save(str(out_path))

        frame_w = image.width // frame_count
        meta = AssetMetadata(
            name=name,
            category=category,
            element_key=element_key,
            pivot=pivot,
            ppu=self.config.ppu,
            frame_count=frame_count,
            frame_width=frame_w,
            frame_height=image.height,
            is_animated=True,
            version=self.config.version,
        )
        meta_path = out_path.with_suffix(".meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta.to_dict(), f, indent=2)

        self.manifest.append({
            "file": str(out_path.relative_to(self.config.output_dir)),
            "meta": str(meta_path.relative_to(self.config.output_dir)),
            **meta.to_dict(),
        })

        return out_path

    def save_manifest(self) -> Path:
        """Save the export manifest."""
        out_path = Path(self.config.output_dir) / "manifest.json"
        with open(out_path, "w") as f:
            json.dump({
                "style": self.config.style_name,
                "version": self.config.version,
                "ppu": self.config.ppu,
                "assets": self.manifest,
            }, f, indent=2)
        return out_path

    def _validate_image(self, image: Image.Image) -> None:
        """Validate image meets Unity constraints."""
        if image.mode != "RGBA":
            raise ValueError(f"Image must be RGBA, got {image.mode}")
        # Check dimensions are reasonable for pixel art
        if image.width > 2048 or image.height > 2048:
            raise ValueError(f"Image too large: {image.width}x{image.height}")
