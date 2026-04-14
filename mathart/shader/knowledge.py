"""ShaderKnowledgeBase — structured Unity shader knowledge for pixel art.

Contains curated shader parameter ranges and rules derived from:
  - Unity URP 2D Renderer documentation
  - Pixel art shader best practices (outline, palette swap, rim light)
  - Distilled knowledge from unity_rules.md and color_light.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ShaderParam:
    """A single shader parameter with its valid range and default."""
    name:        str
    hlsl_type:   str          # "float", "float4", "int", etc.
    min_val:     float
    max_val:     float
    default:     float
    description: str
    art_impact:  str          # "high", "medium", "low"
    knowledge_source: str = ""


@dataclass
class ShaderPreset:
    """A named shader configuration preset."""
    name:        str
    shader_type: str          # "sprite_lit", "outline", "palette_swap", "rim_light"
    params:      dict[str, float]
    description: str
    use_case:    str


class ShaderKnowledgeBase:
    """Structured knowledge about Unity shaders for 2D pixel art.

    This class encodes the mathematical relationships between shader
    parameters and visual quality outcomes, derived from distilled
    knowledge and Unity documentation.

    Shader types covered
    --------------------
    sprite_lit      : URP 2D Lit Sprite (normal map, light response)
    outline         : Pixel-perfect outline shader (Sobel edge detection)
    palette_swap    : Runtime palette replacement (texture LUT)
    rim_light       : Rim/fresnel lighting for 2D sprites
    dissolve        : Noise-based dissolve effect
    pseudo_3d_depth : Depth-simulated pseudo-3D rendering (experimental)
    """

    # ── Sprite Lit parameters ──────────────────────────────────────────────────
    SPRITE_LIT_PARAMS: list[ShaderParam] = [
        ShaderParam("_NormalStrength",  "float",  0.0, 2.0,  1.0,
                    "Normal map influence strength", "high",
                    "knowledge/unity_shader.md"),
        ShaderParam("_AmbientOcclusion","float",  0.0, 1.0,  0.3,
                    "Ambient occlusion intensity", "medium",
                    "knowledge/color_light.md"),
        ShaderParam("_ShadowIntensity", "float",  0.0, 1.0,  0.6,
                    "Shadow darkness (0=no shadow, 1=full black)", "high",
                    "knowledge/color_light.md"),
        ShaderParam("_RimLightWidth",   "float",  0.0, 0.5,  0.1,
                    "Rim light edge width in UV space", "medium",
                    "knowledge/unity_shader.md"),
        ShaderParam("_RimLightIntensity","float", 0.0, 3.0,  1.2,
                    "Rim light brightness multiplier", "medium",
                    "knowledge/color_light.md"),
        ShaderParam("_PixelSnap",       "int",    0,   1,    1,
                    "Enable pixel-perfect snapping (1=on)", "high",
                    "knowledge/unity_rules.md"),
    ]

    # ── Outline shader parameters ──────────────────────────────────────────────
    OUTLINE_PARAMS: list[ShaderParam] = [
        ShaderParam("_OutlineWidth",    "float",  0.5, 4.0,  1.0,
                    "Outline width in pixels (1px = crisp pixel art)", "high",
                    "knowledge/unity_rules.md"),
        ShaderParam("_OutlineAlpha",    "float",  0.0, 1.0,  1.0,
                    "Outline opacity", "medium",
                    "knowledge/unity_shader.md"),
        ShaderParam("_OutlineDepthBias","float", -1.0, 1.0,  0.0,
                    "Depth bias to prevent z-fighting with outline", "low",
                    "knowledge/unity_shader.md"),
    ]

    # ── Palette swap parameters ────────────────────────────────────────────────
    PALETTE_SWAP_PARAMS: list[ShaderParam] = [
        ShaderParam("_PaletteSize",     "int",    2,   32,   8,
                    "Number of colors in the palette LUT", "high",
                    "knowledge/color_science.md"),
        ShaderParam("_ColorTolerance",  "float",  0.0, 0.2,  0.05,
                    "Color matching tolerance for palette swap", "medium",
                    "knowledge/color_science.md"),
        ShaderParam("_DitherStrength",  "float",  0.0, 1.0,  0.0,
                    "Ordered dithering strength (0=off)", "medium",
                    "knowledge/color_science.md"),
    ]

    # ── Pseudo-3D depth parameters (experimental) ──────────────────────────────
    PSEUDO3D_PARAMS: list[ShaderParam] = [
        ShaderParam("_DepthScale",      "float",  0.0, 2.0,  0.5,
                    "Vertical offset scale for depth simulation", "high",
                    "knowledge/differentiable_rendering.md"),
        ShaderParam("_ParallaxLayers",  "int",    1,   8,    3,
                    "Number of parallax depth layers", "high",
                    "knowledge/differentiable_rendering.md"),
        ShaderParam("_IsometricAngle",  "float",  20.0,45.0, 30.0,
                    "Isometric projection angle in degrees", "high",
                    "knowledge/differentiable_rendering.md"),
        ShaderParam("_DepthFogStart",   "float",  0.0, 10.0, 5.0,
                    "Distance at which depth fog begins", "medium",
                    "knowledge/differentiable_rendering.md"),
        ShaderParam("_NormalMapDepth",  "float",  0.0, 2.0,  0.8,
                    "Normal map depth for pseudo-3D lighting", "high",
                    "knowledge/differentiable_rendering.md"),
    ]

    # ── Built-in presets ───────────────────────────────────────────────────────
    PRESETS: list[ShaderPreset] = [
        ShaderPreset(
            name="pixel_art_clean",
            shader_type="sprite_lit",
            params={
                "_NormalStrength": 0.0,
                "_AmbientOcclusion": 0.2,
                "_ShadowIntensity": 0.5,
                "_RimLightWidth": 0.0,
                "_PixelSnap": 1,
            },
            description="Clean pixel art with no normal mapping, subtle shadow",
            use_case="Characters, tiles, UI elements",
        ),
        ShaderPreset(
            name="pixel_art_lit",
            shader_type="sprite_lit",
            params={
                "_NormalStrength": 0.8,
                "_AmbientOcclusion": 0.3,
                "_ShadowIntensity": 0.6,
                "_RimLightWidth": 0.08,
                "_RimLightIntensity": 1.5,
                "_PixelSnap": 1,
            },
            description="Pixel art with normal map lighting and rim light",
            use_case="Boss characters, key items, hero sprites",
        ),
        ShaderPreset(
            name="crisp_outline",
            shader_type="outline",
            params={
                "_OutlineWidth": 1.0,
                "_OutlineAlpha": 1.0,
                "_OutlineDepthBias": 0.0,
            },
            description="1px crisp outline for pixel-perfect art",
            use_case="All pixel art sprites",
        ),
        ShaderPreset(
            name="palette_8color",
            shader_type="palette_swap",
            params={
                "_PaletteSize": 8,
                "_ColorTolerance": 0.04,
                "_DitherStrength": 0.0,
            },
            description="8-color palette swap with tight tolerance",
            use_case="Retro-style characters with strict palette",
        ),
        ShaderPreset(
            name="pseudo_3d_isometric",
            shader_type="pseudo_3d_depth",
            params={
                "_DepthScale": 0.5,
                "_ParallaxLayers": 4,
                "_IsometricAngle": 30.0,
                "_DepthFogStart": 8.0,
                "_NormalMapDepth": 1.0,
            },
            description="Isometric pseudo-3D with parallax layers",
            use_case="Future isometric/pseudo-3D level rendering",
        ),
    ]

    def __init__(self) -> None:
        self._all_params: dict[str, list[ShaderParam]] = {
            "sprite_lit":       self.SPRITE_LIT_PARAMS,
            "outline":          self.OUTLINE_PARAMS,
            "palette_swap":     self.PALETTE_SWAP_PARAMS,
            "pseudo_3d_depth":  self.PSEUDO3D_PARAMS,
        }
        self._presets: dict[str, ShaderPreset] = {
            p.name: p for p in self.PRESETS
        }

    def get_params(self, shader_type: str) -> list[ShaderParam]:
        """Return all parameters for a given shader type."""
        return self._all_params.get(shader_type, [])

    def get_preset(self, name: str) -> Optional[ShaderPreset]:
        """Return a named preset."""
        return self._presets.get(name)

    def list_presets(self) -> list[str]:
        """Return all preset names."""
        return list(self._presets.keys())

    def get_param_ranges(self, shader_type: str) -> dict[str, tuple[float, float]]:
        """Return {param_name: (min, max)} for a shader type."""
        return {
            p.name: (p.min_val, p.max_val)
            for p in self.get_params(shader_type)
        }

    def validate_params(
        self,
        shader_type: str,
        params: dict[str, float],
    ) -> list[str]:
        """Validate shader parameters against known ranges.

        Returns a list of violation messages (empty = valid).
        """
        violations = []
        schema = {p.name: p for p in self.get_params(shader_type)}
        for name, value in params.items():
            if name not in schema:
                violations.append(f"Unknown param '{name}' for shader '{shader_type}'")
                continue
            p = schema[name]
            if not (p.min_val <= value <= p.max_val):
                violations.append(
                    f"'{name}' = {value} out of range [{p.min_val}, {p.max_val}]"
                )
        return violations

    def upgrade_path_report(self) -> str:
        """Return a human-readable upgrade path for shader capabilities."""
        lines = [
            "# Unity Shader Upgrade Path",
            "",
            "## Current State (CPU-side, no Unity required)",
            "- Shader parameter recommendation via quality evaluator",
            "- HLSL code fragment generation",
            "- .shadergraph JSON snippet generation",
            "- Parameter validation against art quality metrics",
            "",
            "## Next Upgrade (requires Unity + URP)",
            "**Trigger**: User installs Unity 2022 LTS + URP package",
            "1. User applies generated shader to sprites in Unity",
            "2. User captures screenshots and runs: `mathart-evolve eval screenshot.png`",
            "3. System scores the render and suggests next parameter adjustment",
            "4. Loop closes: shader params → Unity render → quality score → optimize",
            "",
            "## Pseudo-3D Upgrade (requires normal map pipeline)",
            "**Trigger**: User provides reference 3D model or depth map",
            "1. Generate normal maps from 2D sprites (SpriteIlluminator math)",
            "2. Apply pseudo-3D depth shader with parallax layers",
            "3. Isometric projection math for level rendering",
            "4. Billboard sprite rotation for 360° character views",
            "",
            "## Full 3D Upgrade (requires GPU + PyTorch)",
            "**Trigger**: User provides NVIDIA GPU with CUDA 11.8+",
            "1. nvdiffrast differentiable rasterizer",
            "2. Gradient-based shader parameter optimization",
            "3. Style transfer from reference images",
        ]
        return "\n".join(lines)
