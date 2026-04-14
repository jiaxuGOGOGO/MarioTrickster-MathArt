"""
Palette generation in OKLAB/OKLCH space.

Implements perceptually uniform palette generation with harmony rules
derived from distilled art knowledge (MarioTrickster-Art PROMPT_RECIPES):
  - Warm light → cool shadow (hue shift ~180° in OKLCH)
  - 3-value tonal system (light / mid / dark)
  - Limited palette constraint for pixel art (4-16 colors)
  - Environment color bleeding into shadow

Design constraint (from LevelThemeProfile):
  Each theme needs: ground, platform, wall, background colors + per-element accent.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import json
import numpy as np

from .color_space import (
    oklab_to_srgb, srgb_to_oklab, oklab_to_oklch, oklch_to_oklab,
)


@dataclass
class Palette:
    """A named palette with OKLAB colors and metadata."""
    name: str
    colors_oklab: np.ndarray  # [N, 3]
    roles: list[str] = field(default_factory=list)  # e.g. ["base", "shadow", "highlight", ...]
    metadata: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.colors_oklab)

    @property
    def colors_srgb(self) -> np.ndarray:
        return oklab_to_srgb(self.colors_oklab)

    @property
    def colors_hex(self) -> list[str]:
        srgb = self.colors_srgb
        return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in srgb]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "count": self.count,
            "colors_hex": self.colors_hex,
            "colors_oklab": self.colors_oklab.tolist(),
            "roles": self.roles,
            "metadata": self.metadata,
        }

    def save_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "Palette":
        with open(path) as f:
            d = json.load(f)
        return cls(
            name=d["name"],
            colors_oklab=np.array(d["colors_oklab"]),
            roles=d.get("roles", []),
            metadata=d.get("metadata", {}),
        )


HarmonyType = Literal[
    "complementary", "analogous", "triadic", "split_complementary",
    "warm_cool_shadow", "tonal_ramp",
]


class PaletteGenerator:
    """Generate perceptually uniform palettes using OKLCH harmony rules.

    Distilled knowledge integration:
      - warm_cool_shadow: Implements the "warm light → cool shadow" rule
        from PROMPT_RECIPES §光影. Given a base hue, generates a light/mid/dark
        ramp where shadows shift ~150-180° in hue (complementary temperature).
      - tonal_ramp: Generates a 3-5 value ramp for a single hue, matching
        the "3-value tonal system" from distilled painting knowledge.
    """

    def __init__(self, seed: int | None = None):
        self.rng = np.random.default_rng(seed)

    def generate(
        self,
        harmony: HarmonyType = "warm_cool_shadow",
        base_hue: float | None = None,
        lightness: float = 0.65,
        chroma: float = 0.14,
        count: int = 8,
        name: str = "untitled",
    ) -> Palette:
        """Generate a palette with the specified harmony type.

        Args:
            harmony: Type of color harmony to use.
            base_hue: Base hue in radians [0, 2π]. Random if None.
            lightness: Base lightness [0, 1].
            chroma: Base chroma [0, ~0.4].
            count: Number of colors to generate.
            name: Palette name.

        Returns:
            A Palette object with generated colors.
        """
        if base_hue is None:
            base_hue = self.rng.uniform(0, 2 * np.pi)

        method = getattr(self, f"_gen_{harmony}", None)
        if method is None:
            raise ValueError(f"Unknown harmony type: {harmony}")

        colors_lch, roles = method(base_hue, lightness, chroma, count)
        colors_lab = oklch_to_oklab(colors_lch)

        # Gamut clamp: ensure all colors are representable in sRGB
        colors_lab = self._gamut_clamp(colors_lab)

        return Palette(
            name=name,
            colors_oklab=colors_lab,
            roles=roles,
            metadata={"harmony": harmony, "base_hue_rad": float(base_hue),
                       "lightness": lightness, "chroma": chroma},
        )

    def _gen_complementary(self, h: float, L: float, C: float, n: int):
        """Two opposing hues with tonal ramps."""
        h2 = (h + np.pi) % (2 * np.pi)
        half = n // 2
        colors = []
        roles = []
        for i in range(half):
            t = i / max(half - 1, 1)
            colors.append([L - 0.25 * t, C * (1 - 0.3 * t), h])
            roles.append(f"primary_{i}")
        for i in range(n - half):
            t = i / max(n - half - 1, 1)
            colors.append([L - 0.25 * t, C * (1 - 0.3 * t), h2])
            roles.append(f"complement_{i}")
        return np.array(colors), roles

    def _gen_analogous(self, h: float, L: float, C: float, n: int):
        """Colors spread within ±30° of base hue."""
        spread = np.pi / 6  # 30°
        colors = []
        roles = []
        for i in range(n):
            t = i / max(n - 1, 1)
            hi = h - spread + 2 * spread * t
            li = L - 0.15 * abs(t - 0.5)
            colors.append([li, C, hi % (2 * np.pi)])
            roles.append(f"analogous_{i}")
        return np.array(colors), roles

    def _gen_triadic(self, h: float, L: float, C: float, n: int):
        """Three hues at 120° intervals with tonal variations."""
        hues = [(h + i * 2 * np.pi / 3) % (2 * np.pi) for i in range(3)]
        per_hue = max(n // 3, 1)
        colors = []
        roles = []
        for hi_idx, hi in enumerate(hues):
            for j in range(per_hue):
                t = j / max(per_hue - 1, 1)
                colors.append([L - 0.2 * t, C * (1 - 0.2 * t), hi])
                roles.append(f"triad{hi_idx}_{j}")
        while len(colors) < n:
            colors.append([L * 0.5, C * 0.5, hues[0]])
            roles.append(f"extra_{len(colors)}")
        return np.array(colors[:n]), roles[:n]

    def _gen_split_complementary(self, h: float, L: float, C: float, n: int):
        """Base hue + two hues ±150° from base."""
        h2 = (h + 5 * np.pi / 6) % (2 * np.pi)
        h3 = (h - 5 * np.pi / 6) % (2 * np.pi)
        third = max(n // 3, 1)
        colors = []
        roles = []
        for hue, label in [(h, "base"), (h2, "split_a"), (h3, "split_b")]:
            for j in range(third):
                t = j / max(third - 1, 1)
                colors.append([L - 0.2 * t, C * (1 - 0.2 * t), hue])
                roles.append(f"{label}_{j}")
        while len(colors) < n:
            colors.append([L * 0.4, C * 0.3, h])
            roles.append(f"extra_{len(colors)}")
        return np.array(colors[:n]), roles[:n]

    def _gen_warm_cool_shadow(self, h: float, L: float, C: float, n: int):
        """Warm light → cool shadow palette (distilled from PROMPT_RECIPES §光影).

        Implements the core art rule: when light is warm, shadows shift toward
        cool (complementary) hues. The shadow hue shifts ~150-180° from the
        light hue, with reduced chroma and lightness.
        """
        shadow_hue = (h + np.pi * 0.85) % (2 * np.pi)  # ~153° shift
        highlight_hue = h

        # Build a ramp from highlight → midtone → shadow
        colors = []
        roles = []
        ramp_size = max(n - 2, 3)

        for i in range(ramp_size):
            t = i / max(ramp_size - 1, 1)
            # Lightness: high → low
            li = L + 0.15 * (1 - t) - 0.25 * t
            # Chroma: peaks at midtone, lower at extremes
            ci = C * (0.7 + 0.3 * np.sin(np.pi * t))
            # Hue: interpolate from highlight to shadow
            hi = highlight_hue + t * ((shadow_hue - highlight_hue + np.pi) % (2 * np.pi) - np.pi)
            hi = hi % (2 * np.pi)
            colors.append([li, ci, hi])
            if t < 0.33:
                roles.append("highlight")
            elif t < 0.66:
                roles.append("midtone")
            else:
                roles.append("shadow")

        # Add accent and outline
        colors.append([L + 0.2, C * 1.2, h])
        roles.append("accent")
        colors.append([0.15, C * 0.3, shadow_hue])
        roles.append("outline")

        while len(colors) < n:
            t = self.rng.uniform(0.3, 0.7)
            colors.append([L * t, C * 0.5, (h + self.rng.uniform(-0.3, 0.3)) % (2 * np.pi)])
            roles.append(f"extra_{len(colors)}")

        return np.array(colors[:n]), roles[:n]

    def _gen_tonal_ramp(self, h: float, L: float, C: float, n: int):
        """Generate a tonal ramp for a single hue (3-value system).

        Distilled from painting knowledge: light / mid / dark values
        with subtle hue and chroma shifts for richness.
        """
        colors = []
        roles = ["highlight", "light", "midtone", "dark", "shadow"]
        for i in range(n):
            t = i / max(n - 1, 1)
            li = 0.95 - 0.75 * t
            ci = C * (0.6 + 0.4 * np.sin(np.pi * t))
            hi = h + 0.05 * np.sin(2 * np.pi * t)  # subtle hue shift
            colors.append([li, ci, hi % (2 * np.pi)])
        role_labels = [roles[min(int(i / max(n - 1, 1) * len(roles)), len(roles) - 1)] for i in range(n)]
        return np.array(colors), role_labels

    def _gamut_clamp(self, colors_lab: np.ndarray) -> np.ndarray:
        """Clamp colors to sRGB gamut by reducing chroma."""
        from .color_space import oklab_to_linear
        result = colors_lab.copy()
        for i in range(len(result)):
            rgb = oklab_to_linear(result[i])
            if np.all(rgb >= -0.001) and np.all(rgb <= 1.001):
                continue
            # Binary search for max chroma in gamut
            lch = oklab_to_oklch(result[i:i+1])[0]
            lo, hi = 0.0, lch[1]
            for _ in range(20):
                mid = (lo + hi) / 2
                test_lch = np.array([[lch[0], mid, lch[2]]])
                test_lab = oklch_to_oklab(test_lch)
                test_rgb = oklab_to_linear(test_lab[0])
                if np.all(test_rgb >= -0.001) and np.all(test_rgb <= 1.001):
                    lo = mid
                else:
                    hi = mid
            lch[1] = lo
            result[i] = oklch_to_oklab(np.array([lch]))[0]
        return result

    def generate_theme_palette(
        self,
        theme_name: str = "grassland",
        base_hue: float | None = None,
    ) -> dict[str, Palette]:
        """Generate a complete LevelThemeProfile-compatible palette set.

        Returns palettes for: ground, platform, wall, background, characters, hazards.
        """
        if base_hue is None:
            base_hue = self.rng.uniform(0, 2 * np.pi)

        palettes = {}

        # Ground: earthy tones, base hue shifted toward warm
        ground_hue = (base_hue + 0.3) % (2 * np.pi)
        palettes["ground"] = self.generate(
            "tonal_ramp", base_hue=ground_hue, lightness=0.55,
            chroma=0.08, count=5, name=f"{theme_name}_ground"
        )

        # Platform: slightly cooler than ground
        platform_hue = (base_hue + 0.1) % (2 * np.pi)
        palettes["platform"] = self.generate(
            "tonal_ramp", base_hue=platform_hue, lightness=0.60,
            chroma=0.10, count=5, name=f"{theme_name}_platform"
        )

        # Wall: darker, less saturated
        wall_hue = (base_hue + 0.2) % (2 * np.pi)
        palettes["wall"] = self.generate(
            "tonal_ramp", base_hue=wall_hue, lightness=0.40,
            chroma=0.06, count=5, name=f"{theme_name}_wall"
        )

        # Background: lighter, desaturated
        palettes["background"] = self.generate(
            "analogous", base_hue=base_hue, lightness=0.80,
            chroma=0.05, count=5, name=f"{theme_name}_background"
        )

        # Characters: vibrant, high chroma
        palettes["characters"] = self.generate(
            "split_complementary", base_hue=base_hue, lightness=0.65,
            chroma=0.18, count=8, name=f"{theme_name}_characters"
        )

        # Hazards: warning colors (red-orange-yellow)
        hazard_hue = 0.5  # ~29° in OKLCH, warm red-orange
        palettes["hazards"] = self.generate(
            "warm_cool_shadow", base_hue=hazard_hue, lightness=0.60,
            chroma=0.20, count=6, name=f"{theme_name}_hazards"
        )

        return palettes
