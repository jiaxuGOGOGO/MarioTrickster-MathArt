"""SpriteLibrary — persistent store of analyzed sprite fingerprints.

The library:
  1. Stores all analyzed StyleFingerprints in knowledge/sprite_library.json
  2. Computes aggregate style statistics across all sprites
  3. Exports merged constraints for the parameter space
  4. Provides style references for the AssetEvaluator
  5. Tracks provenance (which sprite contributed which constraint)

The library is append-only: new sprites add knowledge, never overwrite.
Duplicate sprites (same file path + same hash) are skipped silently.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from mathart.sprite.analyzer import SpriteAnalyzer, StyleFingerprint


@dataclass
class LibraryStats:
    """Aggregate statistics across all sprites in the library."""
    total_sprites:    int
    sprite_types:     dict[str, int]        # type -> count
    avg_color_count:  float
    avg_contrast:     float
    avg_edge_density: float
    avg_fill_ratio:   float
    avg_symmetry:     float
    avg_quality:      float
    merged_palette:   list[tuple[int, int, int]]  # Union of all palettes
    merged_constraints: dict[str, tuple[float, float]]  # Merged param ranges

    def summary(self) -> str:
        lines = [
            f"SpriteLibrary: {self.total_sprites} sprites",
            f"  Types: {self.sprite_types}",
            f"  Avg quality: {self.avg_quality:.3f}",
            f"  Avg colors: {self.avg_color_count:.1f} | Avg contrast: {self.avg_contrast:.2f}",
            f"  Avg edge density: {self.avg_edge_density:.2f} | Avg fill: {self.avg_fill_ratio:.2f}",
            f"  Merged palette: {len(self.merged_palette)} colors",
            f"  Constraints: {len(self.merged_constraints)} params",
        ]
        return "\n".join(lines)


class SpriteLibrary:
    """Persistent library of analyzed sprite fingerprints.

    Parameters
    ----------
    project_root : Path
        Root directory of the project (default: current directory).
    analyzer : SpriteAnalyzer, optional
        Custom analyzer instance.
    """

    LIBRARY_FILE = "knowledge/sprite_library.json"
    LOG_FILE     = "SPRITE_LOG.md"

    def __init__(
        self,
        project_root: Optional[Path] = None,
        analyzer: Optional[SpriteAnalyzer] = None,
    ) -> None:
        self.root     = Path(project_root) if project_root else Path.cwd()
        self.analyzer = analyzer or SpriteAnalyzer()
        self._entries: list[dict] = []
        self._hashes:  set[str]   = set()
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_sprite(
        self,
        image: Image.Image,
        source_name: str,
        source_path: str = "",
        sprite_type: str = "unknown",
        tags: Optional[list[str]] = None,
    ) -> tuple[StyleFingerprint, bool]:
        """Analyze and add a sprite to the library.

        Returns
        -------
        (fingerprint, is_new) : tuple
            is_new=False if the sprite was already in the library.
        """
        img_hash = self._image_hash(image)
        if img_hash in self._hashes:
            # Return existing fingerprint
            for entry in self._entries:
                if entry.get("img_hash") == img_hash:
                    fp = self._entry_to_fingerprint(entry)
                    return fp, False
        fp = self.analyzer.analyze(image, source_name, source_path, sprite_type)
        if tags:
            fp.tags.extend(tags)
        entry = fp.to_dict()
        entry["img_hash"] = img_hash
        self._entries.append(entry)
        self._hashes.add(img_hash)
        self._save()
        self._append_log(fp, is_new=True)
        return fp, True

    def add_sprite_file(
        self,
        filepath: str | Path,
        sprite_type: str = "unknown",
        tags: Optional[list[str]] = None,
    ) -> tuple[StyleFingerprint, bool]:
        """Load and add a sprite from a file path."""
        filepath = Path(filepath)
        img = Image.open(filepath)
        return self.add_sprite(
            img,
            source_name=filepath.stem,
            source_path=str(filepath),
            sprite_type=sprite_type,
            tags=tags,
        )

    def add_frames(
        self,
        frames: list[Image.Image],
        source_name: str,
        source_path: str = "",
        sprite_type: str = "character",
    ) -> tuple[StyleFingerprint, bool]:
        """Analyze and add an animation sequence."""
        if not frames:
            raise ValueError("frames list is empty")

        # Use hash of first frame as dedup key
        img_hash = self._image_hash(frames[0]) + f"_anim{len(frames)}"
        if img_hash in self._hashes:
            for entry in self._entries:
                if entry.get("img_hash") == img_hash:
                    return self._entry_to_fingerprint(entry), False

        fp = self.analyzer.analyze_frames(frames, source_name, source_path, sprite_type)
        entry = fp.to_dict()
        entry["img_hash"] = img_hash
        self._entries.append(entry)
        self._hashes.add(img_hash)
        self._save()
        self._append_log(fp, is_new=True)
        return fp, True

    def get_stats(self) -> LibraryStats:
        """Compute aggregate statistics across all sprites."""
        if not self._entries:
            return LibraryStats(
                total_sprites=0,
                sprite_types={},
                avg_color_count=0.0,
                avg_contrast=0.0,
                avg_edge_density=0.0,
                avg_fill_ratio=0.0,
                avg_symmetry=0.0,
                avg_quality=0.0,
                merged_palette=[],
                merged_constraints={},
            )

        types: dict[str, int] = {}
        color_counts, contrasts, edge_densities = [], [], []
        fill_ratios, symmetries, qualities = [], [], []
        all_palette_colors: list[tuple[int, int, int]] = []
        all_constraints: list[dict[str, tuple[float, float]]] = []

        for entry in self._entries:
            t = entry.get("sprite_type", "unknown")
            types[t] = types.get(t, 0) + 1

            color = entry.get("color", {})
            shape = entry.get("shape", {})

            color_counts.append(color.get("color_count", 0))
            contrasts.append(color.get("contrast", 0.0))
            edge_densities.append(shape.get("edge_density", 0.0))
            fill_ratios.append(shape.get("fill_ratio", 0.0))
            symmetries.append(shape.get("symmetry_score", 0.0))
            qualities.append(entry.get("quality_score", 0.0))

            for c in color.get("palette", []):
                all_palette_colors.append(tuple(c))

        # Deduplicate palette
        merged_palette = list(dict.fromkeys(all_palette_colors))[:32]

        # Merge constraints: take the union (widest range) across all sprites
        merged_constraints = self._merge_constraints()

        return LibraryStats(
            total_sprites=len(self._entries),
            sprite_types=types,
            avg_color_count=float(np.mean(color_counts)) if color_counts else 0.0,
            avg_contrast=float(np.mean(contrasts)) if contrasts else 0.0,
            avg_edge_density=float(np.mean(edge_densities)) if edge_densities else 0.0,
            avg_fill_ratio=float(np.mean(fill_ratios)) if fill_ratios else 0.0,
            avg_symmetry=float(np.mean(symmetries)) if symmetries else 0.0,
            avg_quality=float(np.mean(qualities)) if qualities else 0.0,
            merged_palette=merged_palette,
            merged_constraints=merged_constraints,
        )

    def get_best_references(
        self,
        sprite_type: str = "any",
        top_n: int = 5,
    ) -> list[StyleFingerprint]:
        """Return the highest-quality sprites of a given type."""
        entries = self._entries
        if sprite_type != "any":
            entries = [e for e in entries if e.get("sprite_type") == sprite_type]
        entries = sorted(entries, key=lambda e: e.get("quality_score", 0.0), reverse=True)
        return [self._entry_to_fingerprint(e) for e in entries[:top_n]]

    def export_constraints(self) -> dict[str, tuple[float, float]]:
        """Export merged parameter constraints for the RuleCompiler."""
        return self._merge_constraints()

    def export_palette(self) -> list[tuple[int, int, int]]:
        """Export the merged palette from all sprites."""
        stats = self.get_stats()
        return stats.merged_palette

    def count(self) -> int:
        return len(self._entries)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load existing library from JSON file."""
        lib_path = self.root / self.LIBRARY_FILE
        if lib_path.exists():
            try:
                data = json.loads(lib_path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
                self._hashes  = {e.get("img_hash", "") for e in self._entries}
            except (json.JSONDecodeError, KeyError):
                self._entries = []
                self._hashes  = set()

    def _save(self) -> None:
        """Save library to JSON file."""
        lib_path = self.root / self.LIBRARY_FILE
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "count": len(self._entries),
            "entries": self._entries,
        }
        lib_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _append_log(self, fp: StyleFingerprint, is_new: bool) -> None:
        """Append a log entry to SPRITE_LOG.md."""
        log_path = self.root / self.LOG_FILE
        status = "NEW" if is_new else "DUP"
        entry = (
            f"\n## [{status}] {fp.source_name} — {fp.sprite_type}\n"
            f"- **Source**: `{fp.source_path}`\n"
            f"- **Size**: {fp.width}x{fp.height}px\n"
            f"- **Colors**: {fp.color.color_count} | **Quality**: {fp.quality_score:.3f}\n"
            f"- **Tags**: {', '.join(fp.tags) if fp.tags else 'none'}\n"
        )
        if fp.animation:
            entry += (
                f"- **Animation**: {fp.animation.frame_count} frames, "
                f"motion={fp.animation.motion_magnitude:.1f}px/frame\n"
            )
        if not log_path.exists():
            log_path.write_text("# Sprite Library Log\n", encoding="utf-8")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _merge_constraints(self) -> dict[str, tuple[float, float]]:
        """Merge constraints from all sprites (take widest range)."""
        all_constraints: list[dict[str, tuple[float, float]]] = []
        for entry in self._entries:
            fp = self._entry_to_fingerprint(entry)
            all_constraints.append(fp.to_constraints())

        if not all_constraints:
            return {}

        merged: dict[str, tuple[float, float]] = {}
        all_keys = set()
        for c in all_constraints:
            all_keys.update(c.keys())

        for key in all_keys:
            values = [c[key] for c in all_constraints if key in c]
            lo = float(np.mean([v[0] for v in values]))
            hi = float(np.mean([v[1] for v in values]))
            merged[key] = (lo, hi)

        return merged

    @staticmethod
    def _image_hash(image: Image.Image) -> str:
        """Compute a stable hash of an image for deduplication."""
        arr = np.array(image.convert("RGB").resize((32, 32), Image.LANCZOS))
        return hashlib.md5(arr.tobytes()).hexdigest()

    @staticmethod
    def _entry_to_fingerprint(entry: dict) -> StyleFingerprint:
        """Reconstruct a StyleFingerprint from a dict entry."""
        from mathart.sprite.analyzer import (
            ColorProfile, ShapeProfile, AnatomyProfile, AnimationProfile
        )

        color_d = entry.get("color", {})
        palette_raw = color_d.get("palette", [])
        palette = [tuple(c) for c in palette_raw]

        color = ColorProfile(
            palette=palette,
            color_count=color_d.get("color_count", 0),
            hue_spread=color_d.get("hue_spread", 0.0),
            saturation_mean=color_d.get("saturation_mean", 0.0),
            saturation_std=color_d.get("saturation_std", 0.0),
            warm_ratio=color_d.get("warm_ratio", 0.5),
            contrast=color_d.get("contrast", 0.0),
            has_black_outline=color_d.get("has_black_outline", False),
            has_white_highlight=color_d.get("has_white_highlight", False),
        )

        shape_d = entry.get("shape", {})
        shape = ShapeProfile(
            edge_density=shape_d.get("edge_density", 0.0),
            outline_width=shape_d.get("outline_width", 0),
            fill_ratio=shape_d.get("fill_ratio", 0.0),
            symmetry_score=shape_d.get("symmetry_score", 0.5),
            aspect_ratio=shape_d.get("aspect_ratio", 1.0),
            pixel_size=shape_d.get("pixel_size", 1),
        )

        anatomy = None
        if entry.get("anatomy"):
            a = entry["anatomy"]
            anatomy = AnatomyProfile(
                head_ratio=a.get("head_ratio", 0.125),
                width_to_height=a.get("width_to_height", 0.5),
                is_character=a.get("is_character", False),
            )

        animation = None
        if entry.get("animation"):
            an = entry["animation"]
            animation = AnimationProfile(
                frame_count=an.get("frame_count", 1),
                motion_magnitude=an.get("motion_magnitude", 0.0),
                loop_quality=an.get("loop_quality", 1.0),
                timing_uniformity=an.get("timing_uniformity", 1.0),
            )

        return StyleFingerprint(
            source_name=entry.get("source_name", "unknown"),
            source_path=entry.get("source_path", ""),
            sprite_type=entry.get("sprite_type", "unknown"),
            width=entry.get("width", 0),
            height=entry.get("height", 0),
            color=color,
            shape=shape,
            anatomy=anatomy,
            animation=animation,
            quality_score=entry.get("quality_score", 0.0),
            tags=entry.get("tags", []),
        )
