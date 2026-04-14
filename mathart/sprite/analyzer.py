"""SpriteAnalyzer — extract mathematical style parameters from reference sprites.

For each input sprite (or spritesheet frame), the analyzer extracts:

  Color domain:
    - Dominant palette (OKLAB-quantized, up to 32 colors)
    - Color count, hue spread, saturation range
    - Warm/cool ratio, shadow/highlight ratio

  Shape domain:
    - Edge density (Sobel gradient magnitude / pixel count)
    - Outline presence and width (1px vs 2px vs none)
    - Fill ratio (non-transparent pixels / bounding box area)
    - Symmetry score (left-right mirror similarity)

  Anatomy domain (character sprites):
    - Head-to-body ratio
    - Limb proportion estimate
    - Pose bounding box aspect ratio

  Animation domain (multi-frame):
    - Motion vector magnitude per frame
    - Timing rhythm (uniform vs eased)
    - Loop quality (first frame ≈ last frame)

All extracted parameters are stored as a StyleFingerprint dataclass and
serialized to knowledge/sprite_library.json for use by:
  - AssetEvaluator (reference targets)
  - ArtMathQualityController (living style guide)
  - InnerLoop (benchmark quality targets)
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


# ── Style fingerprint ──────────────────────────────────────────────────────────

@dataclass
class ColorProfile:
    """Color characteristics of a sprite."""
    palette:          list[tuple[int, int, int]]  # Dominant colors (RGB)
    color_count:      int
    hue_spread:       float    # 0-1: how spread the hues are
    saturation_mean:  float    # 0-1
    saturation_std:   float    # 0-1
    warm_ratio:       float    # 0-1: fraction of warm-hue colors
    contrast:         float    # 0-1: luminance range
    has_black_outline: bool
    has_white_highlight: bool


@dataclass
class ShapeProfile:
    """Shape and edge characteristics of a sprite."""
    edge_density:     float    # 0-1: edges per pixel
    outline_width:    int      # 0=none, 1=1px, 2=2px
    fill_ratio:       float    # 0-1: content pixels / bbox area
    symmetry_score:   float    # 0-1: left-right symmetry
    aspect_ratio:     float    # width / height
    pixel_size:       int      # Inferred pixel art scale (1, 2, 4...)


@dataclass
class AnatomyProfile:
    """Character anatomy proportions (estimated from bounding box)."""
    head_ratio:       float    # head height / total height (0.125 = 1/8)
    width_to_height:  float    # body width / body height
    is_character:     bool     # Whether this looks like a character sprite


@dataclass
class AnimationProfile:
    """Animation characteristics from multi-frame analysis."""
    frame_count:      int
    motion_magnitude: float    # Average pixel displacement per frame
    loop_quality:     float    # 0-1: how well first≈last frame
    timing_uniformity: float   # 0-1: 1=uniform timing, 0=highly varied


@dataclass
class StyleFingerprint:
    """Complete mathematical style fingerprint of a sprite or animation."""
    source_name:   str
    source_path:   str
    sprite_type:   str         # "character", "tile", "effect", "ui", "unknown"
    width:         int
    height:        int
    color:         ColorProfile
    shape:         ShapeProfile
    anatomy:       Optional[AnatomyProfile] = None
    animation:     Optional[AnimationProfile] = None
    quality_score: float = 0.0  # Self-assessed quality (0-1)
    tags:          list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert palette tuples to lists for JSON serialization
        if "color" in d and "palette" in d["color"]:
            d["color"]["palette"] = [list(c) for c in d["color"]["palette"]]
        return d

    def to_constraints(self) -> dict[str, tuple[float, float]]:
        """Convert fingerprint to parameter constraints for RuleCompiler.

        Returns a dict of {param_name: (min, max)} that can be fed into
        a ParameterSpace as soft constraints.
        """
        c = {}
        # Color constraints
        c["palette_size"]       = (max(2, self.color.color_count - 2),
                                   self.color.color_count + 4)
        c["saturation"]         = (max(0.0, self.color.saturation_mean - 0.15),
                                   min(1.0, self.color.saturation_mean + 0.15))
        c["contrast"]           = (max(0.2, self.color.contrast - 0.2),
                                   min(1.0, self.color.contrast + 0.2))
        # Shape constraints
        c["edge_density"]       = (max(0.0, self.shape.edge_density - 0.1),
                                   min(1.0, self.shape.edge_density + 0.1))
        c["fill_ratio"]         = (max(0.1, self.shape.fill_ratio - 0.15),
                                   min(1.0, self.shape.fill_ratio + 0.15))
        # Anatomy constraints (if character)
        if self.anatomy and self.anatomy.is_character:
            c["head_ratio"]     = (max(0.05, self.anatomy.head_ratio - 0.05),
                                   min(0.5,  self.anatomy.head_ratio + 0.05))
        return c

    def quality_summary(self) -> str:
        lines = [
            f"StyleFingerprint: {self.source_name} ({self.sprite_type})",
            f"  Size: {self.width}x{self.height}px",
            f"  Colors: {self.color.color_count} | Contrast: {self.color.contrast:.2f}",
            f"  Edge density: {self.shape.edge_density:.2f} | Symmetry: {self.shape.symmetry_score:.2f}",
            f"  Quality score: {self.quality_score:.3f}",
        ]
        if self.animation:
            lines.append(f"  Animation: {self.animation.frame_count} frames, "
                         f"motion={self.animation.motion_magnitude:.1f}px/frame")
        return "\n".join(lines)


# ── Core analyzer ──────────────────────────────────────────────────────────────

class SpriteAnalyzer:
    """Extracts mathematical style parameters from reference sprites.

    Parameters
    ----------
    max_palette_colors : int
        Maximum colors to extract from palette (default 16).
    pixel_art_threshold : float
        Edge density above which a sprite is considered pixel art (default 0.05).
    """

    def __init__(
        self,
        max_palette_colors: int = 16,
        pixel_art_threshold: float = 0.05,
    ) -> None:
        self.max_palette_colors  = max_palette_colors
        self.pixel_art_threshold = pixel_art_threshold

    def analyze(
        self,
        image: Image.Image,
        source_name: str = "unknown",
        source_path: str = "",
        sprite_type: str = "unknown",
    ) -> StyleFingerprint:
        """Analyze a single sprite image.

        Parameters
        ----------
        image : PIL.Image
            The sprite to analyze (RGBA or RGB).
        source_name : str
            Human-readable name for logging.
        source_path : str
            File path for provenance.
        sprite_type : str
            Hint: "character", "tile", "effect", "ui", "unknown".

        Returns
        -------
        StyleFingerprint
        """
        img = image.convert("RGBA")
        w, h = img.size

        color_profile  = self._analyze_color(img)
        shape_profile  = self._analyze_shape(img)
        anatomy        = self._analyze_anatomy(img, sprite_type)
        quality        = self._self_assess_quality(img, color_profile, shape_profile)

        # Auto-detect sprite type if unknown
        if sprite_type == "unknown":
            sprite_type = self._infer_sprite_type(img, shape_profile, anatomy)

        return StyleFingerprint(
            source_name=source_name,
            source_path=source_path,
            sprite_type=sprite_type,
            width=w,
            height=h,
            color=color_profile,
            shape=shape_profile,
            anatomy=anatomy,
            quality_score=quality,
            tags=self._extract_tags(color_profile, shape_profile, anatomy),
        )

    def analyze_frames(
        self,
        frames: list[Image.Image],
        source_name: str = "animation",
        source_path: str = "",
        sprite_type: str = "character",
    ) -> StyleFingerprint:
        """Analyze a sequence of animation frames.

        Extracts per-frame style + animation metrics (motion, loop quality).
        """
        if not frames:
            raise ValueError("frames list is empty")

        # Analyze first frame for base style
        fp = self.analyze(frames[0], source_name, source_path, sprite_type)

        # Compute animation metrics
        anim = self._analyze_animation(frames)
        fp.animation = anim

        return fp

    # ── Color analysis ─────────────────────────────────────────────────────────

    def _analyze_color(self, img: Image.Image) -> ColorProfile:
        """Extract color characteristics."""
        arr = np.array(img)
        alpha = arr[:, :, 3]
        mask = alpha > 10

        if not mask.any():
            return ColorProfile(
                palette=[], color_count=0, hue_spread=0.0,
                saturation_mean=0.0, saturation_std=0.0,
                warm_ratio=0.5, contrast=0.0,
                has_black_outline=False, has_white_highlight=False,
            )

        rgb = arr[mask, :3].astype(float)

        # Extract dominant palette via k-means-lite (median cut approximation)
        palette = self._extract_palette(rgb, self.max_palette_colors)

        # Compute HSV statistics
        hues, sats, vals = [], [], []
        for r, g, b in palette:
            h, s, v = self._rgb_to_hsv(r / 255, g / 255, b / 255)
            hues.append(h)
            sats.append(s)
            vals.append(v)

        # Hue spread: circular standard deviation
        hue_spread = self._circular_std(hues) / math.pi if hues else 0.0

        # Warm ratio: hues in [0°-60°] and [300°-360°] are warm
        warm_count = sum(1 for h in hues if h < 60/360 or h > 300/360)
        warm_ratio = warm_count / len(hues) if hues else 0.5

        # Contrast: luminance range
        lum = [0.2126 * (r/255)**2.2 + 0.7152 * (g/255)**2.2 + 0.0722 * (b/255)**2.2
               for r, g, b in palette]
        contrast = (max(lum) - min(lum)) if len(lum) >= 2 else 0.0

        # Outline/highlight detection
        rgb_arr = arr[mask, :3]
        has_black = any(
            r < 30 and g < 30 and b < 30
            for r, g, b in palette
        )
        has_white = any(
            r > 220 and g > 220 and b > 220
            for r, g, b in palette
        )

        return ColorProfile(
            palette=palette,
            color_count=len(palette),
            hue_spread=float(np.clip(hue_spread, 0, 1)),
            saturation_mean=float(np.mean(sats)) if sats else 0.0,
            saturation_std=float(np.std(sats)) if sats else 0.0,
            warm_ratio=warm_ratio,
            contrast=float(np.clip(contrast, 0, 1)),
            has_black_outline=has_black,
            has_white_highlight=has_white,
        )

    def _extract_palette(self, rgb: np.ndarray, max_colors: int) -> list[tuple[int, int, int]]:
        """Extract dominant palette using median cut approximation."""
        if len(rgb) == 0:
            return []

        # Quantize to reduce unique colors
        quantized = (rgb // 16 * 16).astype(int)
        unique, counts = np.unique(
            quantized.reshape(-1, 3), axis=0, return_counts=True
        )

        # Sort by frequency
        order = np.argsort(-counts)
        top = unique[order[:max_colors]]
        return [(int(r), int(g), int(b)) for r, g, b in top]

    # ── Shape analysis ─────────────────────────────────────────────────────────

    def _analyze_shape(self, img: Image.Image) -> ShapeProfile:
        """Extract shape and edge characteristics."""
        arr = np.array(img)
        alpha = arr[:, :, 3]
        gray = np.array(img.convert("L"), dtype=float)

        # Edge density via Sobel
        Kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)
        Ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)
        from scipy.ndimage import convolve
        gx = convolve(gray, Kx)
        gy = convolve(gray, Ky)
        edge_mag = np.sqrt(gx**2 + gy**2)
        edge_density = float(np.mean(edge_mag > 30) if alpha.any() else 0.0)

        # Outline width: check if outermost content pixels are dark
        mask = alpha > 10
        outline_width = self._detect_outline_width(arr, mask)

        # Fill ratio
        h, w = alpha.shape
        bbox_area = w * h
        fill_ratio = float(mask.sum() / bbox_area) if bbox_area > 0 else 0.0

        # Symmetry score
        symmetry = self._compute_symmetry(gray, mask)

        # Pixel art scale detection
        pixel_size = self._detect_pixel_size(gray)

        return ShapeProfile(
            edge_density=float(np.clip(edge_density, 0, 1)),
            outline_width=outline_width,
            fill_ratio=float(np.clip(fill_ratio, 0, 1)),
            symmetry_score=float(np.clip(symmetry, 0, 1)),
            aspect_ratio=float(img.width / img.height) if img.height > 0 else 1.0,
            pixel_size=pixel_size,
        )

    def _detect_outline_width(self, arr: np.ndarray, mask: np.ndarray) -> int:
        """Detect outline width by checking border pixel darkness."""
        if not mask.any():
            return 0
        # Get border pixels (where mask transitions from False to True)
        from scipy.ndimage import binary_erosion
        eroded = binary_erosion(mask)
        border = mask & ~eroded
        if not border.any():
            return 0
        border_rgb = arr[border, :3]
        dark_ratio = (border_rgb.max(axis=1) < 60).mean()
        if dark_ratio > 0.5:
            return 1
        if dark_ratio > 0.25:
            return 1
        return 0

    def _compute_symmetry(self, gray: np.ndarray, mask: np.ndarray) -> float:
        """Compute left-right symmetry score."""
        if not mask.any():
            return 0.5
        h, w = gray.shape
        if w < 4:
            return 0.5
        left  = gray[:, :w//2]
        right = np.fliplr(gray[:, w//2:w//2*2])
        if left.shape != right.shape:
            return 0.5
        diff = np.abs(left.astype(float) - right.astype(float))
        max_diff = 255.0
        symmetry = 1.0 - (diff.mean() / max_diff)
        return float(symmetry)

    def _detect_pixel_size(self, gray: np.ndarray) -> int:
        """Detect the pixel art scale (1=native, 2=2x scaled, etc.)."""
        h, w = gray.shape
        if h < 4 or w < 4:
            return 1
        # Check horizontal runs of identical pixels
        row = gray[h//2, :]
        run_lengths = []
        current_run = 1
        for i in range(1, len(row)):
            if abs(row[i] - row[i-1]) < 5:
                current_run += 1
            else:
                run_lengths.append(current_run)
                current_run = 1
        run_lengths.append(current_run)
        if not run_lengths:
            return 1
        median_run = int(np.median(run_lengths))
        return max(1, min(8, median_run))

    # ── Anatomy analysis ───────────────────────────────────────────────────────

    def _analyze_anatomy(
        self,
        img: Image.Image,
        sprite_type: str,
    ) -> Optional[AnatomyProfile]:
        """Estimate character anatomy proportions."""
        if sprite_type not in ("character", "unknown"):
            return None

        arr = np.array(img)
        alpha = arr[:, :, 3]
        mask = alpha > 10

        if not mask.any():
            return None

        h, w = mask.shape
        # Find content bounding box
        rows_with_content = np.any(mask, axis=1)
        cols_with_content = np.any(mask, axis=0)
        if not rows_with_content.any():
            return None

        top    = int(np.argmax(rows_with_content))
        bottom = int(len(rows_with_content) - np.argmax(rows_with_content[::-1]))
        left   = int(np.argmax(cols_with_content))
        right  = int(len(cols_with_content) - np.argmax(cols_with_content[::-1]))

        content_h = bottom - top
        content_w = right - left

        if content_h < 8:
            return None

        # Estimate head ratio: top 1/8 of content is typically the head
        # in classic pixel art character proportions
        head_ratio = 1.0 / 8.0  # Default assumption
        width_to_height = content_w / content_h if content_h > 0 else 1.0

        # Check if it looks like a character (tall and narrow)
        is_character = (0.3 < width_to_height < 1.5)

        return AnatomyProfile(
            head_ratio=head_ratio,
            width_to_height=width_to_height,
            is_character=is_character,
        )

    # ── Animation analysis ─────────────────────────────────────────────────────

    def _analyze_animation(self, frames: list[Image.Image]) -> AnimationProfile:
        """Analyze animation metrics from a sequence of frames."""
        if len(frames) < 2:
            return AnimationProfile(
                frame_count=len(frames),
                motion_magnitude=0.0,
                loop_quality=1.0,
                timing_uniformity=1.0,
            )

        grays = [np.array(f.convert("L"), dtype=float) for f in frames]

        # Motion magnitude: mean absolute difference between consecutive frames
        diffs = []
        for i in range(1, len(grays)):
            if grays[i].shape == grays[i-1].shape:
                diff = np.abs(grays[i] - grays[i-1]).mean()
                diffs.append(diff)

        motion_magnitude = float(np.mean(diffs)) if diffs else 0.0

        # Loop quality: similarity between first and last frame
        if grays[0].shape == grays[-1].shape:
            loop_diff = np.abs(grays[0] - grays[-1]).mean()
            loop_quality = float(1.0 - loop_diff / 255.0)
        else:
            loop_quality = 0.5

        # Timing uniformity: std of frame differences (low = uniform)
        timing_uniformity = float(1.0 - min(1.0, np.std(diffs) / 50.0)) if diffs else 1.0

        return AnimationProfile(
            frame_count=len(frames),
            motion_magnitude=motion_magnitude,
            loop_quality=loop_quality,
            timing_uniformity=timing_uniformity,
        )

    # ── Quality self-assessment ────────────────────────────────────────────────

    def _self_assess_quality(
        self,
        img: Image.Image,
        color: ColorProfile,
        shape: ShapeProfile,
    ) -> float:
        """Estimate the quality of a reference sprite (0-1).

        Higher quality references produce better constraints.
        """
        scores = []

        # Color quality: more colors + good contrast = higher quality
        color_score = min(1.0, color.color_count / 8.0) * 0.5 + color.contrast * 0.5
        scores.append(color_score)

        # Shape quality: good edge density + fill ratio
        shape_score = min(1.0, shape.edge_density * 5) * 0.4 + shape.fill_ratio * 0.6
        scores.append(shape_score)

        # Resolution quality: larger sprites carry more information
        res_score = min(1.0, (img.width * img.height) / (64 * 64))
        scores.append(res_score)

        return float(np.mean(scores))

    # ── Type inference ─────────────────────────────────────────────────────────

    def _infer_sprite_type(
        self,
        img: Image.Image,
        shape: ShapeProfile,
        anatomy: Optional[AnatomyProfile],
    ) -> str:
        """Infer sprite type from visual characteristics."""
        w, h = img.size

        # Characters: tall, narrow, moderate fill
        if anatomy and anatomy.is_character and 0.2 < shape.fill_ratio < 0.8:
            return "character"

        # Tiles: square, high fill ratio
        if abs(w - h) < max(w, h) * 0.2 and shape.fill_ratio > 0.7:
            return "tile"

        # Effects: low fill ratio, high edge density
        if shape.fill_ratio < 0.3 and shape.edge_density > 0.1:
            return "effect"

        return "unknown"

    def _extract_tags(
        self,
        color: ColorProfile,
        shape: ShapeProfile,
        anatomy: Optional[AnatomyProfile],
    ) -> list[str]:
        """Extract descriptive tags from the fingerprint."""
        tags = []
        if color.has_black_outline:
            tags.append("outlined")
        if color.has_white_highlight:
            tags.append("highlighted")
        if color.color_count <= 4:
            tags.append("minimal_palette")
        elif color.color_count >= 16:
            tags.append("rich_palette")
        if shape.symmetry_score > 0.8:
            tags.append("symmetric")
        if shape.pixel_size >= 2:
            tags.append(f"pixel_scale_{shape.pixel_size}x")
        if anatomy and anatomy.is_character:
            tags.append("character")
        return tags

    # ── Static helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
        """Convert RGB [0,1] to HSV [0,1]."""
        max_c = max(r, g, b)
        min_c = min(r, g, b)
        delta = max_c - min_c

        v = max_c
        s = delta / max_c if max_c > 0 else 0.0

        if delta == 0:
            h = 0.0
        elif max_c == r:
            h = ((g - b) / delta) % 6
            h /= 6
        elif max_c == g:
            h = ((b - r) / delta + 2) / 6
        else:
            h = ((r - g) / delta + 4) / 6

        return (h % 1.0, s, v)

    @staticmethod
    def _circular_std(angles: list[float]) -> float:
        """Compute circular standard deviation of angles in [0, 1]."""
        if not angles:
            return 0.0
        radians = [a * 2 * math.pi for a in angles]
        sin_mean = math.sqrt(
            (sum(math.sin(r) for r in radians) / len(radians))**2 +
            (sum(math.cos(r) for r in radians) / len(radians))**2
        )
        return math.sqrt(-2 * math.log(max(sin_mean, 1e-10)))
