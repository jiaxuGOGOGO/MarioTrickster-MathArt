"""Math Model Registry — versioned catalog of all mathematical models.

This module maintains a registry of all mathematical models used in the
art production pipeline. Each model has:
  - A unique name and version
  - Input/output parameter specifications
  - Quality metrics it affects
  - Knowledge sources that informed it
  - Capability flags (what it can produce)

The registry serves two purposes:
1. Documentation: know what math models exist and what they do
2. Orchestration: the evolution engine can query which models to use
   for a given production task

New models are added as the project evolves — either from internal
development or from distilled external knowledge.

Distilled knowledge applied:
  - Separation of concerns: each model does ONE thing well
  - Composability: models can be chained (output of one → input of next)
  - Versioning: breaking changes increment major version
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


class ModelCapability(str, Enum):
    """What a math model can produce."""
    COLOR_PALETTE = "color_palette"       # Generate color palettes
    PIXEL_IMAGE = "pixel_image"           # Generate pixel art images
    ANIMATION_CURVE = "animation_curve"   # Generate animation curves
    LEVEL_LAYOUT = "level_layout"         # Generate level layouts
    PLANT_GEOMETRY = "plant_geometry"     # Generate plant/organic shapes
    SDF_EFFECT = "sdf_effect"             # Generate SDF-based effects
    SKELETON_POSE = "skeleton_pose"       # Generate skeleton poses
    PHYSICS_SIM = "physics_sim"           # Physics simulation output
    TEXTURE = "texture"                   # Generate textures
    SHADER_PARAMS = "shader_params"       # Compute shader parameters


@dataclass
class ModelEntry:
    """A registered mathematical model.

    Attributes
    ----------
    name : str
        Unique model identifier (e.g., 'oklab_palette_generator').
    version : str
        Semantic version string (e.g., '1.2.0').
    description : str
        Human-readable description of what the model does.
    capabilities : list[ModelCapability]
        What this model can produce.
    module_path : str
        Python import path (e.g., 'mathart.oklab.palette').
    function_name : str
        Entry point function name.
    params : dict[str, dict]
        Parameter specifications: {name: {type, default, range, description}}.
    knowledge_sources : list[str]
        Knowledge files that informed this model.
    quality_metrics : list[str]
        Quality metrics this model affects.
    math_foundation : str
        Brief description of the underlying mathematics.
    status : str
        'stable', 'experimental', 'deprecated'.
    """
    name: str
    version: str
    description: str
    capabilities: list[ModelCapability] = field(default_factory=list)
    module_path: str = ""
    function_name: str = ""
    params: dict[str, dict] = field(default_factory=dict)
    knowledge_sources: list[str] = field(default_factory=list)
    quality_metrics: list[str] = field(default_factory=list)
    math_foundation: str = ""
    status: str = "stable"

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "module_path": self.module_path,
            "function_name": self.function_name,
            "params": self.params,
            "knowledge_sources": self.knowledge_sources,
            "quality_metrics": self.quality_metrics,
            "math_foundation": self.math_foundation,
            "status": self.status,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelEntry":
        caps = [ModelCapability(c) for c in d.get("capabilities", [])]
        return cls(
            name=d["name"],
            version=d["version"],
            description=d.get("description", ""),
            capabilities=caps,
            module_path=d.get("module_path", ""),
            function_name=d.get("function_name", ""),
            params=d.get("params", {}),
            knowledge_sources=d.get("knowledge_sources", []),
            quality_metrics=d.get("quality_metrics", []),
            math_foundation=d.get("math_foundation", ""),
            status=d.get("status", "stable"),
        )


class MathModelRegistry:
    """Registry of all mathematical models in the pipeline.

    Provides discovery, versioning, and capability-based lookup.

    Usage::

        registry = MathModelRegistry()
        palette_models = registry.find_by_capability(ModelCapability.COLOR_PALETTE)
        model = registry.get("oklab_palette_generator")
        registry.save("math_models.json")
    """

    def __init__(self):
        self._models: dict[str, ModelEntry] = {}
        self._register_builtin_models()

    def register(self, model: ModelEntry) -> None:
        """Register a new model or update an existing one."""
        self._models[model.name] = model

    def get(self, name: str) -> Optional[ModelEntry]:
        """Get a model by name."""
        return self._models.get(name)

    def find_by_capability(self, capability: ModelCapability) -> list[ModelEntry]:
        """Find all models with a given capability."""
        return [m for m in self._models.values() if capability in m.capabilities]

    def find_by_status(self, status: str) -> list[ModelEntry]:
        """Find all models with a given status."""
        return [m for m in self._models.values() if m.status == status]

    def list_all(self) -> list[ModelEntry]:
        """List all registered models."""
        return list(self._models.values())

    def save(self, filepath: str | Path) -> None:
        """Save registry to JSON file."""
        filepath = Path(filepath)
        data = {name: model.to_dict() for name, model in self._models.items()}
        filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, filepath: str | Path) -> "MathModelRegistry":
        """Load registry from JSON file."""
        registry = cls.__new__(cls)
        registry._models = {}
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        for name, model_data in data.items():
            registry._models[name] = ModelEntry.from_dict(model_data)
        return registry

    def summary_table(self) -> str:
        """Return a Markdown table summarizing all models."""
        lines = [
            "| 模型名称 | 版本 | 能力 | 数学基础 | 状态 |",
            "|----------|------|------|----------|------|",
        ]
        for model in sorted(self._models.values(), key=lambda m: m.name):
            caps = ", ".join(c.value for c in model.capabilities)
            lines.append(
                f"| `{model.name}` | {model.version} | {caps} | "
                f"{model.math_foundation[:40]} | {model.status} |"
            )
        return "\n".join(lines)

    def _register_builtin_models(self) -> None:
        """Register all built-in mathematical models."""

        # ── Color Science ──
        self.register(ModelEntry(
            name="oklab_palette_generator",
            version="1.1.0",
            description="Generate perceptually harmonious color palettes in OKLAB space",
            capabilities=[ModelCapability.COLOR_PALETTE],
            module_path="mathart.oklab.palette",
            function_name="PaletteGenerator",
            params={
                "base_hue": {"type": "float", "default": 0.0, "range": [0.0, 360.0],
                             "description": "Base hue angle in degrees"},
                "chroma": {"type": "float", "default": 0.12, "range": [0.0, 0.4],
                           "description": "Chroma (saturation) in OKLAB"},
                "n_colors": {"type": "int", "default": 8, "range": [4, 32],
                             "description": "Number of palette colors"},
                "harmony": {"type": "str", "default": "analogous",
                            "description": "Harmony type: analogous/complementary/triadic"},
            },
            knowledge_sources=["knowledge/color_light.md", "knowledge/color_science.md"],
            quality_metrics=["palette_adherence", "color_harmony"],
            math_foundation="OKLAB perceptual color space (Ottosson 2020), circular hue arithmetic",
            status="stable",
        ))

        self.register(ModelEntry(
            name="floyd_steinberg_ditherer",
            version="1.0.0",
            description="Error-diffusion dithering for palette-constrained pixel art",
            capabilities=[ModelCapability.PIXEL_IMAGE],
            module_path="mathart.oklab.quantizer",
            function_name="Quantizer",
            params={
                "palette": {"type": "list", "default": None,
                            "description": "Target color palette (RGB tuples)"},
                "serpentine": {"type": "bool", "default": True,
                               "description": "Use serpentine scanning to reduce banding"},
            },
            knowledge_sources=["knowledge/pixel_art.md"],
            quality_metrics=["palette_adherence", "sharpness"],
            math_foundation="Floyd-Steinberg error diffusion: 7/16, 3/16, 5/16, 1/16 weights",
            status="stable",
        ))

        # ── Procedural Generation ──
        self.register(ModelEntry(
            name="wfc_level_generator",
            version="1.2.0",
            description="Wave Function Collapse algorithm for procedural level generation",
            capabilities=[ModelCapability.LEVEL_LAYOUT],
            module_path="mathart.level.wfc",
            function_name="WFCGenerator",
            params={
                "width": {"type": "int", "default": 20, "range": [5, 200],
                          "description": "Level width in tiles"},
                "height": {"type": "int", "default": 15, "range": [5, 100],
                           "description": "Level height in tiles"},
                "template": {"type": "str", "default": "tutorial_start",
                             "description": "Starting template name"},
            },
            knowledge_sources=["knowledge/level_design.md", "knowledge/game_design.md"],
            quality_metrics=["rule_compliance"],
            math_foundation="Constraint propagation, Shannon entropy minimization",
            status="stable",
        ))

        self.register(ModelEntry(
            name="lsystem_plant_generator",
            version="1.0.0",
            description="L-System grammar-based procedural plant generation",
            capabilities=[ModelCapability.PLANT_GEOMETRY, ModelCapability.PIXEL_IMAGE],
            module_path="mathart.sdf.lsystem",
            function_name="LSystemRenderer",
            params={
                "preset": {"type": "str", "default": "oak",
                           "description": "Plant preset: oak/shrub/vine/fern/flower"},
                "iterations": {"type": "int", "default": 4, "range": [2, 8],
                               "description": "L-System iteration depth"},
                "angle": {"type": "float", "default": 25.0, "range": [10.0, 60.0],
                          "description": "Branch angle in degrees"},
            },
            knowledge_sources=["knowledge/plant_botany.md"],
            quality_metrics=["sharpness", "style_consistency"],
            math_foundation="Lindenmayer system formal grammar, turtle graphics geometry",
            status="stable",
        ))

        # ── Animation ──
        self.register(ModelEntry(
            name="spring_damper_animator",
            version="1.1.0",
            description="Spring-damper physics for secondary animation (capes, hair, accessories)",
            capabilities=[ModelCapability.ANIMATION_CURVE, ModelCapability.PHYSICS_SIM],
            module_path="mathart.animation.skeleton",
            function_name="SpringDamper",
            params={
                "spring_k": {"type": "float", "default": 15.0, "range": [1.0, 100.0],
                             "description": "Spring stiffness constant (Hooke's Law k)"},
                "damping_c": {"type": "float", "default": 4.0, "range": [0.1, 20.0],
                              "description": "Damping coefficient"},
                "mass": {"type": "float", "default": 1.0, "range": [0.1, 10.0],
                         "description": "Simulated mass"},
            },
            knowledge_sources=["knowledge/physics_sim.md", "knowledge/animation.md"],
            quality_metrics=["style_consistency"],
            math_foundation="Hooke's Law F=-kx-cv, second-order ODE, Verlet integration",
            status="stable",
        ))

        self.register(ModelEntry(
            name="fabrik_ik_solver",
            version="1.0.0",
            description="FABRIK inverse kinematics solver for 2D character limbs",
            capabilities=[ModelCapability.SKELETON_POSE, ModelCapability.ANIMATION_CURVE],
            module_path="mathart.animation.skeleton",
            function_name="FABRIKSolver",
            params={
                "max_iterations": {"type": "int", "default": 10, "range": [1, 50],
                                   "description": "Maximum FABRIK iterations"},
                "tolerance": {"type": "float", "default": 0.001, "range": [0.0001, 0.1],
                              "description": "Convergence tolerance"},
            },
            knowledge_sources=["knowledge/anatomy.md", "knowledge/physics_sim.md"],
            quality_metrics=["rule_compliance"],
            math_foundation="FABRIK: Forward And Backward Reaching Inverse Kinematics",
            status="stable",
        ))

        # ── SDF Effects ──
        self.register(ModelEntry(
            name="sdf_effect_renderer",
            version="1.0.0",
            description="SDF-based procedural effects (fire, water, magic, spikes)",
            capabilities=[ModelCapability.SDF_EFFECT, ModelCapability.PIXEL_IMAGE],
            module_path="mathart.sdf.effects",
            function_name="EffectRenderer",
            params={
                "effect_type": {"type": "str", "default": "fire",
                                "description": "Effect type: fire/water/magic/spike/explosion"},
                "intensity": {"type": "float", "default": 1.0, "range": [0.1, 3.0],
                              "description": "Effect intensity multiplier"},
                "color_temp": {"type": "float", "default": 0.0, "range": [-1.0, 1.0],
                               "description": "Color temperature shift (-1=cool, +1=warm)"},
            },
            knowledge_sources=["knowledge/vfx.md", "knowledge/sdf_math.md"],
            quality_metrics=["sharpness", "color_harmony"],
            math_foundation="Signed Distance Fields, Perlin noise, SDF boolean operations",
            status="stable",
        ))

        # ── New: Differentiable Rendering (Experimental) ──
        self.register(ModelEntry(
            name="differentiable_renderer_2d",
            version="0.1.0",
            description="Experimental 2D differentiable renderer for parameter optimization",
            capabilities=[ModelCapability.PIXEL_IMAGE, ModelCapability.SHADER_PARAMS],
            module_path="mathart.evolution.diff_render",
            function_name="DifferentiableRenderer2D",
            params={
                "learning_rate": {"type": "float", "default": 0.01, "range": [0.0001, 0.1],
                                  "description": "Gradient descent learning rate"},
                "iterations": {"type": "int", "default": 100, "range": [10, 1000],
                               "description": "Optimization iterations"},
                "loss_type": {"type": "str", "default": "perceptual",
                              "description": "Loss function: perceptual/l2/style"},
            },
            knowledge_sources=["knowledge/differentiable_rendering.md", "knowledge/pbr_math.md"],
            quality_metrics=["sharpness", "style_consistency"],
            math_foundation=(
                "Differentiable rasterization, gradient-based parameter optimization, "
                "perceptual loss (LPIPS-inspired)"
            ),
            status="experimental",
        ))

        # ── New: Asset Quality Evaluator ──
        self.register(ModelEntry(
            name="asset_quality_evaluator",
            version="1.0.0",
            description="Multi-metric automated quality evaluation for pixel art assets",
            capabilities=[ModelCapability.PIXEL_IMAGE],
            module_path="mathart.evaluator.evaluator",
            function_name="AssetEvaluator",
            params={
                "pass_threshold": {"type": "float", "default": 0.55, "range": [0.3, 0.95],
                                   "description": "Minimum score to pass quality check"},
                "sharpness_weight": {"type": "float", "default": 0.30, "range": [0.0, 1.0],
                                     "description": "Weight for sharpness metric"},
            },
            knowledge_sources=["knowledge/pixel_art.md", "knowledge/unity_rules.md"],
            quality_metrics=["sharpness", "palette_adherence", "contrast", "style_consistency",
                             "color_harmony"],
            math_foundation=(
                "Laplacian variance (sharpness), Michelson contrast, "
                "pHash DCT fingerprinting, OKLAB color analysis"
            ),
            status="stable",
        ))
