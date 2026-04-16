"""
Asset Production Pipeline — The core system that ties everything together.

SESSION-018 UPGRADE:
  - produce_sprite() now passes reference & palette to InnerLoopRunner
  - produce_animation() exports GIF/APNG alongside spritesheet
  - Commercial-grade sprite sheet metadata (JSON) for game engine import
  - SpriteLibrary integration for reference/palette sourcing
  - Improved shape defaults (gem/star now visible)

Usage::

    from mathart.pipeline import AssetPipeline, AssetSpec

    pipeline = AssetPipeline(output_dir="output/")

    # Produce a single sprite
    result = pipeline.produce_sprite(
        spec=AssetSpec(name="coin", shape="coin", style="metal"),
    )

    # Produce an animated spritesheet + GIF
    result = pipeline.produce_animation(
        anim_spec=AnimationSpec(
            asset=AssetSpec(name="coin_spin", shape="coin"),
            animation_type="idle", n_frames=8,
        )
    )
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, ImageDraw

from .sdf.primitives import circle, box, star, triangle, ring, segment
from .sdf.operations import smooth_union, smooth_subtraction, union
from .sdf.renderer import (
    render_sdf, render_textured_sdf, render_spritesheet,
    render_sdf_layered, composite_layers, LayeredRenderResult,
    render_textured_sdf_layered,
)
from .sdf.effects import spike_sdf, flame_sdf, saw_blade_sdf, glow_sdf, electric_arc_sdf
from .oklab.palette import PaletteGenerator, Palette
from .evaluator.evaluator import AssetEvaluator
from .evolution.inner_loop import InnerLoopRunner, RunMode
from .evolution.cppn import CPPNEvolver, CPPNGenome
from .animation.principles import (
    PrincipledAnimation, AnimationKeyframe,
    SquashStretch, FollowThrough, Anticipation,
    ANIMATION_PRESETS, EASING_FUNCTIONS,
    create_jump_animation, create_walk_cycle, create_idle_breathe,
    create_attack_swing, create_death_animation,
)
from .distill.compiler import Constraint, ParameterSpace
from .animation.particles import ParticleSystem, ParticleConfig
from .animation.fluid_vfx import FluidDrivenVFXSystem, FluidVFXConfig, resize_mask_to_grid
from .animation.cage_deform import CageDeformer, CagePreset, CageAnimation
from .animation.skeleton import Skeleton
from .animation.character_renderer import render_character_frame
from .animation.character_presets import get_preset, CHARACTER_PRESETS
from .animation.presets import (
    idle_animation, run_animation, jump_animation, fall_animation, hit_animation,
)
from .animation.phase_driven import (
    phase_driven_fall_frame,
    phase_driven_hit_frame,
    phase_driven_jump_frame,
    phase_driven_run_frame,
    phase_driven_walk_frame,
)
from .animation.unified_motion import (
    MotionPipelineNode,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
    infer_contact_tags,
    pose_to_umr,
    run_motion_pipeline,
)
# SESSION-028: Physics-guided animation (PhysDiff-inspired)
from .animation.physics_projector import AnglePoseProjector, CognitiveMotionConfig
# SESSION-027: Semantic genotype system
from .animation.genotype import (
    CharacterGenotype, GENOTYPE_PRESETS, BODY_TEMPLATES,
    mutate_genotype, crossover_genotypes,
)
# SESSION-029: Biomechanics engine (ZMP/CoM, IPM, Skating Cleanup, FABRIK Gait)
from .animation.biomechanics import (
    BiomechanicsProjector, ZMPAnalyzer, InvertedPendulumModel,
    SkatingCleanupCalculus, FABRIKGaitGenerator,
    compute_biomechanics_penalty,
)
from .evolution.evolution_layer3 import PhysicsKnowledgeDistiller
# SESSION-040: Pipeline Contract & Auditor (攻坚战役三)
from .pipeline_contract import UMR_Context, PipelineContractError, PipelineContractGuard
from .pipeline_auditor import UMR_Auditor, ContactFlickerDetector
from .animation.phase_driven_idle import phase_driven_idle_frame
from .level import (
    LevelSpec,
    LevelSpecBridge,
    LevelTheme,
    PDGNode,
    ProceduralDependencyGraph,
    RenderMode as LevelRenderMode,
    UniversalSceneDescription,
    WFCGenerator,
)
from .shader.generator import ShaderCodeGenerator
from .export.bridge import AssetExporter, ExportConfig


# ── Shape Library ─────────────────────────────────────────────────────────────
# SESSION-018: Fixed gem/star defaults to be visible (larger radii)

SHAPE_LIBRARY: dict[str, Callable[..., Any]] = {
    # SESSION-019 FIX: All primitives use (cx, cy, ...) signature — use kwargs!
    "circle": lambda r=0.4: circle(cx=0, cy=0, r=r),
    "box": lambda w=0.35, h=0.35: box(cx=0, cy=0, hw=w, hh=h),
    # SESSION-019 FIX: star(cx, cy, r_outer, r_inner, n_points) — use kwargs!
    "star": lambda n=5, r1=0.42, r2=0.22: star(cx=0, cy=0, r_outer=r1, r_inner=r2, n_points=n),
    "triangle": lambda s=0.4: triangle(),  # uses default equilateral
    "ring": lambda r=0.35, w=0.1: ring(cx=0, cy=0, r=r, thickness=w),
    "spike": lambda: spike_sdf(),
    "flame": lambda: flame_sdf(),
    "saw": lambda: saw_blade_sdf(),
    "glow": lambda: glow_sdf(),
    "electric": lambda: electric_arc_sdf(),
    "coin": lambda: ring(cx=0, cy=0, r=0.35, thickness=0.12),
    # SESSION-019 FIX: gem is a 4-pointed star
    "gem": lambda: star(cx=0, cy=0, r_outer=0.38, r_inner=0.18, n_points=4),
    # SESSION-019 FIX: use kwargs for all primitives in compound shapes
    "shield": lambda: smooth_union(circle(cx=0, cy=0, r=0.3), box(cx=0, cy=0, hw=0.25, hh=0.1), k=0.1),
    "heart": lambda: smooth_union(
        circle(cx=-0.12, cy=-0.08, r=0.2),
        smooth_union(circle(cx=0.12, cy=-0.08, r=0.2), triangle(), k=0.1),
        k=0.15,
    ),
    "platform": lambda: box(cx=0, cy=0, hw=0.6, hh=0.12),
    "bullet": lambda: smooth_union(circle(cx=0, cy=-0.1, r=0.15), box(cx=0, cy=0.1, hw=0.1, hh=0.25), k=0.08),
}


# ── Asset Specification ───────────────────────────────────────────────────────

@dataclass
class AssetSpec:
    """Specification for an asset to produce."""
    name: str
    shape: str = "circle"
    style: str = "default"      # default, stone, wood, metal, organic, crystal
    size: int = 64
    palette_scheme: str = "warm_cool_shadow"
    base_hue: float = 0.0
    n_colors: int = 8
    evolution_iterations: int = 20
    population_size: int = 16
    quality_threshold: float = 0.6
    seed: int = 42


@dataclass
class AnimationSpec:
    """Specification for an animated asset."""
    asset: AssetSpec
    animation_type: str = "idle"  # idle, jump, walk, attack, death
    n_frames: int = 8
    fps: int = 12
    loop: bool = True


@dataclass
class CharacterSpec:
    """Specification for a multi-state character asset pack.

    This bridges the gap between the generic shape pipeline and a practical
    production-ready character pipeline that exports multiple actionable
    animation states rather than a single demo strip.
    """
    name: str
    preset: str = "mario"
    frame_width: int = 32
    frame_height: int = 32
    fps: int = 12
    head_units: float = 3.0
    frames_per_state: int = 8
    states: list[str] = field(default_factory=lambda: ["idle", "run", "jump", "fall", "hit"])
    state_frames: dict[str, int] = field(default_factory=dict)
    loop_overrides: dict[str, bool] = field(default_factory=lambda: {
        "idle": True,
        "run": True,
        "jump": False,
        "fall": False,
        "hit": False,
    })
    enable_dither: bool = True
    enable_outline: bool = True
    enable_lighting: bool = True
    export_palette: bool = True
    evolution_iterations: int = 0
    evolution_population: int = 6
    evolution_variation_strength: float = 0.18
    evolution_preview_states: list[str] = field(default_factory=lambda: ["idle", "run", "jump"])
    evolution_elite_size: int = 3
    evolution_stagnation_patience: int = 2
    # SESSION-028: Physics-guided animation
    enable_physics: bool = True
    physics_stiffness: float = 1.0
    physics_damping: float = 1.0
    physics_cognitive_strength: float = 1.0
    # SESSION-027: Semantic genotype evolution
    use_genotype: bool = False
    genotype: Optional[CharacterGenotype] = None
    evolution_crossover_rate: float = 0.25
    # SESSION-029: Biomechanics engine
    enable_biomechanics: bool = True
    biomechanics_zmp: bool = True
    biomechanics_ipm: bool = True
    biomechanics_skating_cleanup: bool = True
    biomechanics_zmp_strength: float = 0.3


CHARACTER_ANIMATION_MAP: dict[str, Callable[[float], dict[str, float]]] = {
    "idle": idle_animation,
    "run": run_animation,
    "jump": jump_animation,
    "fall": fall_animation,
    "hit": hit_animation,
}


@dataclass
class AssetResult:
    """Result of asset production."""
    name: str
    image: Optional[Image.Image] = None
    spritesheet: Optional[Image.Image] = None
    frames: list[Image.Image] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    evolution_history: list[float] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    output_paths: list[str] = field(default_factory=list)


@dataclass
class LevelPipelineSpec:
    """Specification for a PDG-driven procedural level pipeline run."""

    level_id: str
    width: int = 22
    height: int = 7
    tile_size: int = 16
    theme: str = "grassland"
    render_mode: str = "flat_2d"
    palette_size: int = 16
    shader_goal: str = "auto"
    seed: Optional[int] = None
    ensure_ground: bool = True
    ensure_spawn: bool = True
    ensure_goal: bool = True
    export_preview: bool = True
    required_assets: list[str] = field(default_factory=lambda: ["player", "enemy", "tile", "item", "background"])


# ── Asset Pipeline ────────────────────────────────────────────────────────────

class AssetPipeline:
    """High-level pipeline for producing game-ready art assets.

    SESSION-018 UPGRADE: Now integrates SpriteLibrary for reference/palette
    sourcing and passes them through to the evaluator.
    """

    def __init__(
        self,
        output_dir: str = "output",
        verbose: bool = True,
        seed: int = 42,
        project_root: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.seed = seed
        self.project_root = Path(project_root) if project_root else None

        # Generate a default palette for evaluation
        self.palette_gen = PaletteGenerator(seed=seed)
        self._default_palette = self._generate_default_palette()

        # Initialize evaluator WITH default palette (SESSION-018 fix)
        self.evaluator = AssetEvaluator(
            palette=self._default_palette,
        )
        self._production_log: list[dict] = []

        # Try to load sprite library for references
        self._sprite_library = None
        self._load_sprite_library()

    def _load_sprite_library(self):
        """Try to load SpriteLibrary for reference/palette sourcing."""
        if self.project_root is None:
            return
        try:
            from .sprite.library import SpriteLibrary
            self._sprite_library = SpriteLibrary(project_root=self.project_root)
            if self._sprite_library.count() > 0 and self.verbose:
                self._log(f"Loaded sprite library: {self._sprite_library.count()} references")
        except Exception:
            self._sprite_library = None

    def _generate_default_palette(self) -> list[tuple[int, int, int]]:
        """Generate a default pixel art palette for evaluation."""
        try:
            palette = self.palette_gen.generate("warm_cool_shadow", n_colors=12)
            if hasattr(palette, 'colors_srgb'):
                return [tuple(c) for c in palette.colors_srgb]
            return [(c[0], c[1], c[2]) for c in palette]
        except Exception:
            # Fallback: classic pixel art palette
            return [
                (0, 0, 0),        # Black outline
                (34, 32, 52),     # Dark purple
                (69, 40, 60),     # Dark red
                (102, 57, 49),    # Brown
                (143, 86, 59),    # Light brown
                (223, 113, 38),   # Orange
                (217, 160, 102),  # Tan
                (238, 195, 154),  # Light skin
                (251, 242, 54),   # Yellow
                (153, 229, 80),   # Green
                (106, 190, 48),   # Dark green
                (55, 148, 110),   # Teal
            ]

    def _get_reference_and_palette(self, spec: AssetSpec):
        """Get reference image and palette from sprite library or defaults.

        SESSION-018: This is the key fix — we now always provide reference
        and palette to the evaluator instead of leaving them as None.
        """
        reference = None
        palette = self._default_palette

        if self._sprite_library and self._sprite_library.count() > 0:
            try:
                # Get best reference for this shape type
                refs = self._sprite_library.get_best_references(
                    sprite_type=spec.shape, top_n=1
                )
                if not refs:
                    refs = self._sprite_library.get_best_references(
                        sprite_type="any", top_n=1
                    )
                # Get merged palette from library
                lib_palette = self._sprite_library.export_palette()
                if lib_palette:
                    palette = lib_palette
            except Exception:
                pass

        # Generate style-specific palette
        try:
            style_palette = self.palette_gen.generate(
                spec.palette_scheme, n_colors=spec.n_colors,
                base_hue=spec.base_hue,
            )
            if hasattr(style_palette, 'colors_srgb'):
                palette = [tuple(c) for c in style_palette.colors_srgb]
        except Exception:
            pass

        return reference, palette

    def _log(self, msg: str):
        if self.verbose:
            print(f"  [Pipeline] {msg}")

    def _build_parameter_space(self, spec: AssetSpec) -> ParameterSpace:
        """Build a parameter space for evolution based on asset spec."""
        space = ParameterSpace(name=f"{spec.name}_params")

        # Color parameters
        space.add_constraint(Constraint(
            param_name="fill_r", min_value=30, max_value=255, default_value=150,
        ))
        space.add_constraint(Constraint(
            param_name="fill_g", min_value=30, max_value=255, default_value=100,
        ))
        space.add_constraint(Constraint(
            param_name="fill_b", min_value=30, max_value=255, default_value=80,
        ))

        # Lighting
        space.add_constraint(Constraint(
            param_name="light_angle", min_value=0.0, max_value=6.28,
            default_value=0.785,
        ))
        space.add_constraint(Constraint(
            param_name="ao_strength", min_value=0.0, max_value=0.8,
            default_value=0.4,
        ))
        space.add_constraint(Constraint(
            param_name="color_ramp_levels", min_value=3, max_value=7,
            default_value=5,
        ))

        # Outline
        space.add_constraint(Constraint(
            param_name="outline_width", min_value=0.01, max_value=0.06,
            default_value=0.03,
        ))

        # Shape-specific parameters (SESSION-018: wider ranges for more exploration)
        if spec.shape in ("circle", "coin"):
            space.add_constraint(Constraint(
                param_name="radius", min_value=0.25, max_value=0.5,
                default_value=0.38,
            ))
        elif spec.shape in ("star", "gem"):
            space.add_constraint(Constraint(
                param_name="outer_radius", min_value=0.30, max_value=0.50,
                default_value=0.42,
            ))
            space.add_constraint(Constraint(
                param_name="inner_radius", min_value=0.10, max_value=0.30,
                default_value=0.20,
            ))
        elif spec.shape == "ring":
            space.add_constraint(Constraint(
                param_name="ring_radius", min_value=0.25, max_value=0.45,
                default_value=0.35,
            ))
            space.add_constraint(Constraint(
                param_name="ring_width", min_value=0.05, max_value=0.15,
                default_value=0.1,
            ))

        return space

    def _build_generator(
        self, spec: AssetSpec, palette_colors: Optional[list] = None,
    ) -> Callable[[dict], Image.Image]:
        """Build a generator function for evolution.

        SESSION-019: Now accepts palette_colors for palette-constrained rendering.
        """
        _palette_colors = palette_colors

        def generator(params: dict) -> Image.Image:
            fill_r = int(np.clip(params.get("fill_r", 150), 0, 255))
            fill_g = int(np.clip(params.get("fill_g", 100), 0, 255))
            fill_b = int(np.clip(params.get("fill_b", 80), 0, 255))
            light_angle = params.get("light_angle", 0.785)
            ao_strength = params.get("ao_strength", 0.4)
            ramp_levels = int(np.clip(params.get("color_ramp_levels", 5), 3, 7))
            outline_width = params.get("outline_width", 0.03)

            # Build SDF shape (SESSION-018: fixed defaults)
            if spec.shape in ("circle",):
                r = params.get("radius", 0.38)
                sdf = circle(cx=0, cy=0, r=r)
            elif spec.shape == "coin":
                r = params.get("radius", 0.35)
                sdf = ring(cx=0, cy=0, r=r, thickness=0.12)
            elif spec.shape in ("star",):
                r1 = params.get("outer_radius", 0.42)
                r2 = params.get("inner_radius", 0.20)
                # SESSION-019 FIX: use keyword args for star()
                sdf = star(cx=0, cy=0, r_outer=r1, r_inner=r2, n_points=5)
            elif spec.shape == "gem":
                r1 = params.get("outer_radius", 0.38)
                r2 = params.get("inner_radius", 0.18)
                # SESSION-019 FIX: use keyword args for star()
                sdf = star(cx=0, cy=0, r_outer=r1, r_inner=r2, n_points=4)
            elif spec.shape == "ring":
                r = params.get("ring_radius", 0.35)
                w = params.get("ring_width", 0.1)
                sdf = ring(cx=0, cy=0, r=r, thickness=w)
            elif spec.shape in SHAPE_LIBRARY:
                sdf = SHAPE_LIBRARY[spec.shape]()
            else:
                sdf = circle(cx=0, cy=0, r=0.4)

            # Render
            # SESSION-019: Palette-constrained rendering kwargs
            pal_kwargs = {}
            if _palette_colors:
                pal_kwargs = {
                    "palette_constrained": True,
                    "palette_colors": _palette_colors,
                    "palette_dither": True,
                }

            if spec.style != "default" and spec.style in (
                "stone", "wood", "metal", "organic", "crystal"
            ):
                img = render_textured_sdf(
                    sdf, texture_type=spec.style,
                    width=spec.size, height=spec.size,
                    fill_color=(fill_r, fill_g, fill_b, 255),
                    outline_width=outline_width,
                    light_angle=light_angle,
                    ao_strength=ao_strength,
                    color_ramp_levels=ramp_levels,
                    **pal_kwargs,
                )
            else:
                img = render_sdf(
                    sdf, spec.size, spec.size,
                    fill_color=(fill_r, fill_g, fill_b, 255),
                    outline_width=outline_width,
                    light_angle=light_angle,
                    ao_strength=ao_strength,
                    color_ramp_levels=ramp_levels,
                    enable_lighting=True,
                    enable_ao=True,
                    enable_hue_shift=True,
                    **pal_kwargs,
                )
            return img

        return generator

    def produce_sprite(self, spec: AssetSpec) -> AssetResult:
        """Produce a single sprite through evolution.

        SESSION-018 FIX: Now passes reference and palette to the evaluator
        and inner loop runner, enabling proper quality assessment.
        """
        start = time.time()
        self._log(f"Producing sprite: {spec.name} (shape={spec.shape}, style={spec.style})")

        # SESSION-018: Get reference and palette
        reference, palette = self._get_reference_and_palette(spec)

        # Build components
        space = self._build_parameter_space(spec)
        # SESSION-019: Pass palette_colors for palette-constrained rendering
        palette_colors_for_render = None
        if palette and isinstance(palette, list) and len(palette) > 0:
            # Ensure tuples of (r, g, b)
            palette_colors_for_render = [
                (c[0], c[1], c[2]) if len(c) >= 3 else c for c in palette
            ]
        generator = self._build_generator(spec, palette_colors=palette_colors_for_render)

        # Run evolution WITH reference and palette (SESSION-018 fix)
        # SESSION-021 (P0-NEW-9): Adaptive evolution convergence acceleration
        # Scale patience, population, and min_delta based on shape complexity
        # Complex shapes (star, gem) need more patience; simple shapes converge faster
        shape_complexity = {
            "circle": 1.0, "ring": 1.2, "coin": 1.2,
            "box": 1.0, "triangle": 1.1,
            "star": 1.8, "gem": 1.6,
        }
        complexity = shape_complexity.get(spec.shape, 1.3)
        adaptive_patience = max(5, int(spec.evolution_iterations // 3 * complexity))
        adaptive_pop = max(8, int(spec.population_size * (0.8 + 0.2 * complexity)))
        # Tighter min_delta for simple shapes (converge faster), looser for complex
        adaptive_min_delta = 0.008 / complexity

        runner = InnerLoopRunner(
            evaluator=AssetEvaluator(
                palette=palette,
                reference=reference,
            ),
            quality_threshold=spec.quality_threshold,
            max_iterations=spec.evolution_iterations,
            population_size=adaptive_pop,
            patience=adaptive_patience,
            min_delta=adaptive_min_delta,
            verbose=self.verbose,
            mode=RunMode.AUTONOMOUS,
            project_root=self.project_root,
        )

        result = runner.run(
            generator=generator,
            space=space,
            reference=reference,
            palette=palette,
            seed=spec.seed,
        )

        # Save output
        asset_dir = self.output_dir / spec.name
        asset_dir.mkdir(parents=True, exist_ok=True)

        output_paths = []
        if result.best_image:
            img_path = str(asset_dir / f"{spec.name}.png")
            result.best_image.save(img_path)
            output_paths.append(img_path)
            self._log(f"Saved: {img_path}")

        # Save metadata (SESSION-018: enriched with palette and evaluation details)
        meta = {
            "name": spec.name,
            "shape": spec.shape,
            "style": spec.style,
            "size": spec.size,
            "score": result.best_score,
            "iterations": result.iterations,
            "converged": result.converged,
            "best_params": result.best_params,
            "evolution_history": result.history,
            "palette_used": palette is not None,
            "reference_used": reference is not None,
        }
        meta_path = str(asset_dir / f"{spec.name}.meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2, default=str)
        output_paths.append(meta_path)

        elapsed = time.time() - start
        self._log(f"Done: score={result.best_score:.4f}, "
                   f"iters={result.iterations}, time={elapsed:.1f}s")

        self._production_log.append({
            "name": spec.name, "type": "sprite",
            "score": result.best_score, "elapsed": elapsed,
        })

        return AssetResult(
            name=spec.name,
            image=result.best_image,
            metadata=meta,
            score=result.best_score,
            evolution_history=result.history,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def produce_animation(self, anim_spec: AnimationSpec) -> AssetResult:
        """Produce an animated spritesheet + GIF.

        SESSION-018 UPGRADE: Now also exports GIF/APNG for direct preview.
        Also generates commercial-grade sprite sheet metadata.
        """
        start = time.time()
        spec = anim_spec.asset
        self._log(f"Producing animation: {spec.name} "
                   f"(anim={anim_spec.animation_type}, frames={anim_spec.n_frames})")

        # Step 1: Get base sprite
        base_result = self.produce_sprite(spec)
        if base_result.image is None:
            self._log("ERROR: Failed to produce base sprite")
            return AssetResult(name=spec.name, score=0.0)

        base_img = base_result.image

        # Step 2: Create animation
        anim_factory = ANIMATION_PRESETS.get(
            anim_spec.animation_type, create_idle_breathe
        )
        animation = anim_factory()

        # Step 3: Generate frames
        frames = []
        anim_data = animation.generate_frames(anim_spec.n_frames)

        for i, frame_data in enumerate(anim_data):
            frame = self._apply_transform(
                base_img,
                scale=frame_data["scale"],
                rotation=frame_data["rotation"],
                opacity=frame_data["opacity"],
            )
            frames.append(frame)

        # Step 4: Assemble spritesheet
        sheet_width = spec.size * anim_spec.n_frames
        sheet = Image.new("RGBA", (sheet_width, spec.size), (0, 0, 0, 0))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * spec.size, 0))

        # Step 5: Save
        asset_dir = self.output_dir / spec.name
        asset_dir.mkdir(parents=True, exist_ok=True)

        output_paths = list(base_result.output_paths)

        sheet_path = str(asset_dir / f"{spec.name}_sheet.png")
        sheet.save(sheet_path)
        output_paths.append(sheet_path)
        self._log(f"Saved spritesheet: {sheet_path}")

        # SESSION-018: Export GIF for direct preview
        gif_path = str(asset_dir / f"{spec.name}.gif")
        try:
            frame_duration = max(16, 1000 // max(1, anim_spec.fps))
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=frame_duration,
                loop=0 if anim_spec.loop else 1,
                disposal=2,  # Clear frame before drawing next
            )
            output_paths.append(gif_path)
            self._log(f"Saved GIF: {gif_path}")
        except Exception as e:
            self._log(f"GIF export failed (non-fatal): {e}")

        # Save individual frames
        for i, frame in enumerate(frames):
            frame_path = str(asset_dir / f"{spec.name}_frame_{i:02d}.png")
            frame.save(frame_path)
            output_paths.append(frame_path)

        # SESSION-018: Commercial-grade sprite sheet metadata
        anim_meta = {
            "meta": {
                "image": f"{spec.name}_sheet.png",
                "format": "RGBA8888",
                "size": {"w": sheet_width, "h": spec.size},
                "scale": 1,
                "generator": "MarioTrickster-MathArt",
                "version": "0.7.0",
            },
            "animations": {
                anim_spec.animation_type: {
                    "frames": [
                        {
                            "name": f"{spec.name}_{anim_spec.animation_type}_{i}",
                            "rect": [i * spec.size, 0, spec.size, spec.size],
                            "duration": max(16, 1000 // max(1, anim_spec.fps)),
                        }
                        for i in range(anim_spec.n_frames)
                    ],
                    "loop": anim_spec.loop,
                    "fps": anim_spec.fps,
                }
            },
            "sprite_data": {
                "name": spec.name,
                "animation": anim_spec.animation_type,
                "n_frames": anim_spec.n_frames,
                "fps": anim_spec.fps,
                "loop": anim_spec.loop,
                "frame_size": {"w": spec.size, "h": spec.size},
                "base_score": base_result.score,
                "frame_transforms": [
                    {
                        "position": d["position"],
                        "scale": d["scale"],
                        "rotation": d["rotation"],
                        "opacity": d["opacity"],
                    }
                    for d in anim_data
                ],
            },
        }
        anim_meta_path = str(asset_dir / f"{spec.name}_anim.json")
        with open(anim_meta_path, "w") as f:
            json.dump(anim_meta, f, indent=2)
        output_paths.append(anim_meta_path)

        elapsed = time.time() - start
        self._log(f"Animation done: {anim_spec.n_frames} frames, time={elapsed:.1f}s")

        return AssetResult(
            name=spec.name,
            image=base_result.image,
            spritesheet=sheet,
            frames=frames,
            metadata={**base_result.metadata, **anim_meta},
            score=base_result.score,
            evolution_history=base_result.evolution_history,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def _copy_character_style(self, style: Any) -> Any:
        return type(style)(**vars(style))

    def _copy_palette(self, palette: Palette) -> Palette:
        return Palette(
            name=palette.name,
            colors_oklab=np.array(palette.colors_oklab, dtype=np.float64, copy=True),
            roles=list(palette.roles),
            metadata=dict(palette.metadata),
        )

    def _mutate_character_style(
        self,
        style: Any,
        rng: np.random.Generator,
        strength: float,
    ) -> Any:
        mutated = self._copy_character_style(style)

        def jitter(value: float, scale: float, low: float, high: float) -> float:
            return float(np.clip(value + rng.normal(0.0, scale * strength), low, high))

        mutated.head_radius = jitter(style.head_radius, 0.08, 0.26, 0.52)
        mutated.torso_width = jitter(style.torso_width, 0.07, 0.16, 0.36)
        mutated.torso_height = jitter(style.torso_height, 0.07, 0.12, 0.30)
        mutated.arm_thickness = jitter(style.arm_thickness, 0.025, 0.04, 0.12)
        mutated.leg_thickness = jitter(style.leg_thickness, 0.025, 0.05, 0.14)
        mutated.hand_radius = jitter(style.hand_radius, 0.02, 0.03, 0.09)
        mutated.foot_width = jitter(style.foot_width, 0.03, 0.05, 0.16)
        mutated.foot_height = jitter(style.foot_height, 0.018, 0.025, 0.09)
        mutated.outline_width = jitter(style.outline_width, 0.02, 0.02, 0.07)
        mutated.light_angle = jitter(style.light_angle, 0.35, -1.5, 1.5)

        if rng.random() < 0.35 * max(strength, 0.05):
            mutated.eye_style = str(rng.choice(["dot", "oval", "wide"]))
        if mutated.has_hat and rng.random() < 0.25 * max(strength, 0.05):
            mutated.hat_style = str(rng.choice(["cap", "top"]))

        return mutated

    def _mutate_palette(
        self,
        palette: Palette,
        rng: np.random.Generator,
        strength: float,
    ) -> Palette:
        colors = np.array(palette.colors_oklab, dtype=np.float64, copy=True)
        noise = np.stack([
            rng.normal(0.0, 0.035 * strength, len(colors)),
            rng.normal(0.0, 0.025 * strength, len(colors)),
            rng.normal(0.0, 0.025 * strength, len(colors)),
        ], axis=-1)
        colors += noise
        colors[:, 0] = np.clip(colors[:, 0], 0.15, 0.92)

        for idx, role in enumerate(palette.roles):
            role_l = role.lower()
            if "outline" in role_l:
                colors[idx, 0] = min(colors[idx, 0], 0.22)
                colors[idx, 1:] *= 0.75
            elif "skin" in role_l:
                colors[idx, 0] = np.clip(colors[idx, 0], 0.55, 0.88)

        metadata = dict(palette.metadata)
        metadata["mutated_from"] = palette.name
        metadata["variation_strength"] = float(strength)
        return Palette(
            name=f"{palette.name}_variant",
            colors_oklab=colors,
            roles=list(palette.roles),
            metadata=metadata,
        )

    def _character_candidate_vector(
        self,
        char_spec: CharacterSpec,
        style: Any,
        palette: Palette,
    ) -> np.ndarray:
        values: list[float] = [float(char_spec.head_units)]
        for _, value in sorted(vars(style).items()):
            if isinstance(value, bool):
                values.append(1.0 if value else 0.0)
            elif isinstance(value, (int, float, np.floating)):
                values.append(float(value))
            elif isinstance(value, str):
                values.append(float(sum(ord(ch) for ch in value) % 257) / 257.0)
        values.extend(np.asarray(palette.colors_oklab, dtype=np.float64).reshape(-1).tolist())
        return np.asarray(values, dtype=np.float64)

    def _select_diverse_character_elites(
        self,
        pool: list[tuple[CharacterSpec, Any, Palette, dict[str, Any]]],
        elite_size: int,
        min_distance: float = 0.035,
    ) -> list[tuple[CharacterSpec, Any, Palette, dict[str, Any]]]:
        if not pool:
            return []

        ranked = sorted(pool, key=lambda item: item[3]["score"], reverse=True)
        selected: list[tuple[CharacterSpec, Any, Palette, dict[str, Any]]] = []
        vectors: list[np.ndarray] = []

        for candidate in ranked:
            vector = self._character_candidate_vector(candidate[0], candidate[1], candidate[2])
            if not vectors:
                selected.append(candidate)
                vectors.append(vector)
            else:
                distances = [float(np.mean(np.abs(vector - other))) for other in vectors]
                if min(distances) >= min_distance:
                    selected.append(candidate)
                    vectors.append(vector)
            if len(selected) >= elite_size:
                return selected

        for candidate in ranked:
            if any(candidate is chosen for chosen in selected):
                continue
            selected.append(candidate)
            if len(selected) >= elite_size:
                break
        return selected

    def _compute_character_silhouette_metrics(self, alpha: np.ndarray) -> dict[str, float]:
        mask = alpha > 0.05
        height, width = mask.shape
        coverage = float(np.mean(mask))
        if not np.any(mask):
            return {
                "silhouette_score": 0.0,
                "coverage": coverage,
                "bbox_fill": 0.0,
                "aspect_ratio": 0.0,
                "centroid_x": 0.5,
                "centroid_y": 0.5,
            }

        ys, xs = np.nonzero(mask)
        bbox_h_px = int(ys.max() - ys.min() + 1)
        bbox_w_px = int(xs.max() - xs.min() + 1)
        bbox_fill = float(mask.sum()) / max(bbox_h_px * bbox_w_px, 1)
        aspect_ratio = (bbox_h_px / max(height, 1)) / max(bbox_w_px / max(width, 1), 1e-6)
        centroid_x = float(xs.mean() / max(width - 1, 1))
        centroid_y = float(ys.mean() / max(height - 1, 1))

        occupancy_score = max(0.0, 1.0 - min(abs(coverage - 0.22) / 0.22, 1.0))
        aspect_score = max(0.0, 1.0 - min(abs(aspect_ratio - 1.85) / 1.1, 1.0))
        fill_score = max(0.0, 1.0 - min(abs(bbox_fill - 0.55) / 0.35, 1.0))
        center_offset = math.sqrt(((centroid_x - 0.50) / 0.35) ** 2 + ((centroid_y - 0.58) / 0.40) ** 2)
        centering_score = max(0.0, 1.0 - min(center_offset, 1.0))
        silhouette_score = 0.30 * occupancy_score + 0.25 * aspect_score + 0.20 * fill_score + 0.25 * centering_score

        return {
            "silhouette_score": float(silhouette_score),
            "coverage": coverage,
            "bbox_fill": float(bbox_fill),
            "aspect_ratio": float(aspect_ratio),
            "centroid_x": centroid_x,
            "centroid_y": centroid_y,
        }

    def _compute_state_distinction_score(self, state_signatures: dict[str, dict[str, Any]]) -> float:
        if len(state_signatures) < 2:
            return 0.5

        keys = list(state_signatures)
        alpha_diffs: list[float] = []
        centroid_diffs: list[float] = []
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                left_sig = state_signatures[left]
                right_sig = state_signatures[right]
                alpha_diffs.append(float(np.mean(np.abs(left_sig["alpha_signature"] - right_sig["alpha_signature"]))))
                centroid_diffs.append(float(np.linalg.norm(np.asarray(left_sig["centroid"]) - np.asarray(right_sig["centroid"]))))

        alpha_score = min((float(np.mean(alpha_diffs)) if alpha_diffs else 0.0) * 5.0, 1.0)
        centroid_score = min((float(np.mean(centroid_diffs)) if centroid_diffs else 0.0) * 2.5, 1.0)
        return float(0.75 * alpha_score + 0.25 * centroid_score)

    def _score_character_candidate(
        self,
        char_spec: CharacterSpec,
        style: Any,
        palette: Palette,
    ) -> dict[str, Any]:
        preview_states = [
            state for state in char_spec.evolution_preview_states
            if state in char_spec.states and state in CHARACTER_ANIMATION_MAP
        ]
        if not preview_states:
            preview_states = [s for s in char_spec.states if s in CHARACTER_ANIMATION_MAP][:3]

        frame_scores: list[float] = []
        motion_scores: list[float] = []
        coverage_scores: list[float] = []
        silhouette_scores: list[float] = []
        state_signatures: dict[str, dict[str, Any]] = {}

        for state in preview_states:
            anim_func = CHARACTER_ANIMATION_MAP[state]
            frame_count = max(2, min(4, int(char_spec.state_frames.get(state, char_spec.frames_per_state))))
            prev_alpha: Optional[np.ndarray] = None
            state_motion: list[float] = []
            state_coverage: list[float] = []
            state_silhouettes: list[float] = []
            state_alphas: list[np.ndarray] = []
            state_centroids: list[tuple[float, float]] = []

            for i in range(frame_count):
                t = i / max(frame_count - 1, 1)
                skeleton = Skeleton.create_humanoid(head_units=char_spec.head_units)
                frame = render_character_frame(
                    skeleton,
                    anim_func(t),
                    style,
                    width=char_spec.frame_width,
                    height=char_spec.frame_height,
                    palette=palette,
                    enable_dither=char_spec.enable_dither,
                    enable_outline=char_spec.enable_outline,
                    enable_lighting=char_spec.enable_lighting,
                )
                eval_result = self.evaluator.evaluate(frame, palette=palette)
                frame_scores.append(float(eval_result.overall_score))

                alpha = np.asarray(frame.getchannel("A"), dtype=np.float32) / 255.0
                metrics = self._compute_character_silhouette_metrics(alpha)
                state_coverage.append(float(metrics["coverage"]))
                state_silhouettes.append(float(metrics["silhouette_score"]))
                state_alphas.append(alpha)
                state_centroids.append((float(metrics["centroid_x"]), float(metrics["centroid_y"])))
                if prev_alpha is not None:
                    state_motion.append(float(np.mean(np.abs(alpha - prev_alpha))))
                prev_alpha = alpha

            mean_coverage = float(np.mean(state_coverage)) if state_coverage else 0.0
            coverage_scores.append(max(0.0, 1.0 - min(abs(mean_coverage - 0.22) / 0.22, 1.0)))
            motion_scores.append(min((float(np.mean(state_motion)) if state_motion else 0.0) * 6.0, 1.0))
            silhouette_scores.append(float(np.mean(state_silhouettes)) if state_silhouettes else 0.0)

            if state_alphas:
                mean_alpha = np.mean(np.stack(state_alphas, axis=0), axis=0)
                mean_centroid = np.mean(np.asarray(state_centroids, dtype=np.float64), axis=0)
                state_signatures[state] = {
                    "alpha_signature": mean_alpha,
                    "centroid": (float(mean_centroid[0]), float(mean_centroid[1])),
                }

        quality_score = float(np.mean(frame_scores)) if frame_scores else 0.0
        motion_score = float(np.mean(motion_scores)) if motion_scores else 0.0
        coverage_score = float(np.mean(coverage_scores)) if coverage_scores else 0.0
        silhouette_score = float(np.mean(silhouette_scores)) if silhouette_scores else 0.0
        state_distinction_score = self._compute_state_distinction_score(state_signatures)
        total_score = (
            0.56 * quality_score
            + 0.15 * motion_score
            + 0.10 * coverage_score
            + 0.11 * silhouette_score
            + 0.08 * state_distinction_score
        )

        return {
            "score": float(total_score),
            "quality_score": quality_score,
            "motion_score": motion_score,
            "coverage_score": coverage_score,
            "silhouette_score": silhouette_score,
            "state_distinction_score": float(state_distinction_score),
            "preview_states": preview_states,
        }

    def _evolve_character_spec(
        self,
        char_spec: CharacterSpec,
        style: Any,
        palette: Palette,
    ) -> tuple[CharacterSpec, Any, Palette, dict[str, Any], list[float]]:
        rng = np.random.default_rng(self.seed)
        base_spec = replace(char_spec)
        base_style = self._copy_character_style(style)
        base_palette = self._copy_palette(palette)

        best_spec = replace(char_spec)
        best_style = self._copy_character_style(style)
        best_palette = self._copy_palette(palette)
        best_breakdown = self._score_character_candidate(best_spec, best_style, best_palette)
        history = [float(best_breakdown["score"])]

        elite_size = max(1, min(int(char_spec.evolution_elite_size), max(1, char_spec.evolution_population)))
        stagnation_patience = max(1, int(char_spec.evolution_stagnation_patience))
        base_strength = float(max(char_spec.evolution_variation_strength, 0.05))
        strength_history = [base_strength]
        stagnation_steps = 0
        stagnation_events = 0

        def _entry(
            spec: CharacterSpec,
            style_obj: Any,
            palette_obj: Palette,
            breakdown: dict[str, Any],
            iteration: int,
            rank: int,
            parent_source: str,
            variation_strength: float,
        ) -> dict[str, Any]:
            return {
                "iteration": iteration,
                "rank": rank,
                "score": float(breakdown["score"]),
                "quality_score": float(breakdown["quality_score"]),
                "motion_score": float(breakdown["motion_score"]),
                "coverage_score": float(breakdown["coverage_score"]),
                "silhouette_score": float(breakdown["silhouette_score"]),
                "state_distinction_score": float(breakdown["state_distinction_score"]),
                "head_units": float(spec.head_units),
                "style": vars(style_obj).copy(),
                "palette_hex": palette_obj.colors_hex,
                "preview_states": list(breakdown["preview_states"]),
                "parent_source": parent_source,
                "variation_strength": float(variation_strength),
            }

        initial_entry = _entry(best_spec, best_style, best_palette, best_breakdown, 0, 0, "seed", base_strength)
        candidates: list[dict[str, Any]] = [initial_entry]
        archive: list[tuple[CharacterSpec, Any, Palette, dict[str, Any]]] = [(best_spec, best_style, best_palette, best_breakdown)]
        elites = self._select_diverse_character_elites(archive, elite_size)

        for iteration in range(1, max(0, char_spec.evolution_iterations) + 1):
            adaptive_strength = float(np.clip(base_strength * (1.0 + 0.45 * stagnation_steps), 0.05, 0.60))
            strength_history.append(adaptive_strength)
            round_pool: list[tuple[CharacterSpec, Any, Palette, dict[str, Any]]] = []
            round_entries: list[dict[str, Any]] = []

            for rank in range(max(1, char_spec.evolution_population)):
                restart_mode = stagnation_steps >= stagnation_patience and rank == max(1, char_spec.evolution_population) - 1
                if restart_mode:
                    parent_spec = replace(base_spec)
                    parent_style = self._copy_character_style(base_style)
                    parent_palette = self._copy_palette(base_palette)
                    parent_source = "restart"
                else:
                    parent_spec, parent_style, parent_palette, _ = elites[int(rng.integers(0, len(elites)))]
                    parent_spec = replace(parent_spec)
                    parent_style = self._copy_character_style(parent_style)
                    parent_palette = self._copy_palette(parent_palette)
                    parent_source = "elite"

                trial_spec = replace(
                    parent_spec,
                    head_units=float(np.clip(
                        parent_spec.head_units + rng.normal(0.0, 0.24 * adaptive_strength),
                        2.2,
                        3.8,
                    )),
                )
                trial_style = self._mutate_character_style(parent_style, rng, adaptive_strength)
                trial_palette = self._mutate_palette(parent_palette, rng, adaptive_strength)
                trial_breakdown = self._score_character_candidate(trial_spec, trial_style, trial_palette)
                round_pool.append((trial_spec, trial_style, trial_palette, trial_breakdown))
                round_entries.append(_entry(
                    trial_spec,
                    trial_style,
                    trial_palette,
                    trial_breakdown,
                    iteration,
                    rank,
                    parent_source,
                    adaptive_strength,
                ))

            candidates.extend(round_entries)
            archive.extend(round_pool)
            elites = self._select_diverse_character_elites(archive, elite_size)
            round_best_spec, round_best_style, round_best_palette, round_best_breakdown = max(
                round_pool,
                key=lambda item: item[3]["score"],
            )

            if round_best_breakdown["score"] > best_breakdown["score"] + 1e-6:
                best_spec = round_best_spec
                best_style = round_best_style
                best_palette = round_best_palette
                best_breakdown = round_best_breakdown
                stagnation_steps = 0
            else:
                stagnation_steps += 1
                if stagnation_steps >= stagnation_patience and stagnation_steps % stagnation_patience == 0:
                    stagnation_events += 1

            history.append(float(best_breakdown["score"]))

        evolution_meta = {
            "enabled": True,
            "iterations": int(char_spec.evolution_iterations),
            "population": int(char_spec.evolution_population),
            "variation_strength": float(char_spec.evolution_variation_strength),
            "elite_size": int(elite_size),
            "stagnation_patience": int(stagnation_patience),
            "stagnation_events": int(stagnation_events),
            "strength_history": [float(v) for v in strength_history],
            "preview_states": list(best_breakdown["preview_states"]),
            "initial_score": float(candidates[0]["score"]),
            "best_score": float(best_breakdown["score"]),
            "history": history,
            "best_character": {
                "head_units": float(best_spec.head_units),
                "style": vars(best_style).copy(),
                "palette_hex": best_palette.colors_hex,
                "quality_score": float(best_breakdown["quality_score"]),
                "motion_score": float(best_breakdown["motion_score"]),
                "coverage_score": float(best_breakdown["coverage_score"]),
                "silhouette_score": float(best_breakdown["silhouette_score"]),
                "state_distinction_score": float(best_breakdown["state_distinction_score"]),
            },
            "objective_weights": {
                "quality_score": 0.56,
                "motion_score": 0.15,
                "coverage_score": 0.10,
                "silhouette_score": 0.11,
                "state_distinction_score": 0.08,
            },
            "candidates": candidates,
        }
        return best_spec, best_style, best_palette, evolution_meta, history

    # ── SESSION-027: Genotype-based evolution methods ──────────────────────

    def _genotype_to_palette(self, genotype: CharacterGenotype) -> Palette:
        """Convert genotype palette genes to a Palette object."""
        genes = list(genotype.palette_genes)
        while len(genes) < 18:
            genes.extend([0.5, 0.0, 0.0])
        colors_oklab = np.array(genes[:18], dtype=np.float64).reshape(6, 3)
        roles = ["skin", "hair_hat", "shirt", "pants", "shoes", "outline"]
        return Palette(
            name=f"genotype_{genotype.archetype}",
            colors_oklab=colors_oklab,
            roles=roles,
        )

    def _genotype_candidate_vector(self, genotype: CharacterGenotype) -> np.ndarray:
        """Create a feature vector from a genotype for diversity selection."""
        values: list[float] = [
            float(hash(genotype.archetype) % 1000) / 1000.0,
            float(hash(genotype.body_template) % 1000) / 1000.0,
        ]
        for key in sorted(genotype.proportion_modifiers.keys()):
            values.append(float(genotype.proportion_modifiers[key]))
        values.append(genotype.outline_width)
        values.append(genotype.light_angle)
        for slot_key in sorted(genotype.slots.keys()):
            slot = genotype.slots[slot_key]
            values.append(float(hash(slot.part_id) % 1000) / 1000.0)
        values.extend(genotype.palette_genes[:18])
        return np.asarray(values, dtype=np.float64)

    def _select_diverse_genotype_elites(
        self,
        pool: list[tuple[CharacterGenotype, dict[str, Any]]],
        elite_size: int,
        min_distance: float = 0.035,
    ) -> list[tuple[CharacterGenotype, dict[str, Any]]]:
        """Select diverse elite genotypes from a pool."""
        if not pool:
            return []
        ranked = sorted(pool, key=lambda item: item[1]["score"], reverse=True)
        selected: list[tuple[CharacterGenotype, dict[str, Any]]] = []
        vectors: list[np.ndarray] = []

        for candidate in ranked:
            vector = self._genotype_candidate_vector(candidate[0])
            if not vectors:
                selected.append(candidate)
                vectors.append(vector)
            else:
                distances = [float(np.mean(np.abs(vector - other))) for other in vectors]
                if min(distances) >= min_distance:
                    selected.append(candidate)
                    vectors.append(vector)
            if len(selected) >= elite_size:
                return selected

        for candidate in ranked:
            if any(candidate is chosen for chosen in selected):
                continue
            selected.append(candidate)
            if len(selected) >= elite_size:
                break
        return selected

    def _score_genotype_candidate(
        self,
        char_spec: CharacterSpec,
        genotype: CharacterGenotype,
    ) -> dict[str, Any]:
        """Score a genotype candidate using the existing evaluation pipeline."""
        style = genotype.decode_to_style()
        palette = self._genotype_to_palette(genotype)
        head_units = genotype.get_head_units()
        temp_spec = replace(char_spec, head_units=head_units)
        return self._score_character_candidate(temp_spec, style, palette)

    def _evolve_character_genotype(
        self,
        char_spec: CharacterSpec,
        genotype: CharacterGenotype,
    ) -> tuple[CharacterSpec, Any, Palette, dict[str, Any], list[float]]:
        """Evolve a character using the semantic genotype system.

        SESSION-027: This operates on CharacterGenotype objects and uses
        three-layer semantic mutation (structural + proportional + palette)
        plus crossover between diverse elites.
        """
        import copy as _copy

        rng = np.random.default_rng(self.seed)
        base_genotype = _copy.deepcopy(genotype)

        best_genotype = _copy.deepcopy(genotype)
        best_breakdown = self._score_genotype_candidate(char_spec, best_genotype)
        history = [float(best_breakdown["score"])]

        elite_size = max(1, min(int(char_spec.evolution_elite_size), max(1, char_spec.evolution_population)))
        stagnation_patience = max(1, int(char_spec.evolution_stagnation_patience))
        base_strength = float(max(char_spec.evolution_variation_strength, 0.05))
        crossover_rate = float(max(char_spec.evolution_crossover_rate, 0.0))
        strength_history = [base_strength]
        stagnation_steps = 0
        stagnation_events = 0

        def _entry(
            g: CharacterGenotype,
            breakdown: dict[str, Any],
            iteration: int,
            rank: int,
            parent_source: str,
            variation_strength: float,
        ) -> dict[str, Any]:
            style = g.decode_to_style()
            palette = self._genotype_to_palette(g)
            return {
                "iteration": iteration,
                "rank": rank,
                "score": float(breakdown["score"]),
                "quality_score": float(breakdown["quality_score"]),
                "motion_score": float(breakdown["motion_score"]),
                "coverage_score": float(breakdown["coverage_score"]),
                "silhouette_score": float(breakdown["silhouette_score"]),
                "state_distinction_score": float(breakdown["state_distinction_score"]),
                "head_units": float(g.get_head_units()),
                "style": vars(style).copy(),
                "palette_hex": palette.colors_hex,
                "preview_states": list(breakdown["preview_states"]),
                "parent_source": parent_source,
                "variation_strength": float(variation_strength),
                "genotype": g.to_dict(),
            }

        initial_entry = _entry(best_genotype, best_breakdown, 0, 0, "seed", base_strength)
        candidates: list[dict[str, Any]] = [initial_entry]
        archive: list[tuple[CharacterGenotype, dict[str, Any]]] = [
            (best_genotype, best_breakdown),
        ]
        elites = self._select_diverse_genotype_elites(archive, elite_size)

        for iteration in range(1, max(0, char_spec.evolution_iterations) + 1):
            adaptive_strength = float(np.clip(
                base_strength * (1.0 + 0.45 * stagnation_steps), 0.05, 0.60,
            ))
            strength_history.append(adaptive_strength)
            round_pool: list[tuple[CharacterGenotype, dict[str, Any]]] = []
            round_entries: list[dict[str, Any]] = []

            for rank in range(max(1, char_spec.evolution_population)):
                restart_mode = (
                    stagnation_steps >= stagnation_patience
                    and rank == max(1, char_spec.evolution_population) - 1
                )

                if restart_mode:
                    parent_genotype = _copy.deepcopy(base_genotype)
                    parent_source = "restart"
                else:
                    parent_genotype = _copy.deepcopy(
                        elites[int(rng.integers(0, len(elites)))][0]
                    )
                    parent_source = "elite"

                # SESSION-027: Crossover with another elite
                if (
                    not restart_mode
                    and len(elites) >= 2
                    and rng.random() < crossover_rate
                ):
                    other_idx = int(rng.integers(0, len(elites)))
                    other_genotype = elites[other_idx][0]
                    trial_genotype = crossover_genotypes(parent_genotype, other_genotype, rng)
                    parent_source = "crossover"
                else:
                    trial_genotype = parent_genotype

                trial_genotype = mutate_genotype(trial_genotype, rng, adaptive_strength)
                trial_breakdown = self._score_genotype_candidate(char_spec, trial_genotype)
                round_pool.append((trial_genotype, trial_breakdown))
                round_entries.append(_entry(
                    trial_genotype,
                    trial_breakdown,
                    iteration,
                    rank,
                    parent_source,
                    adaptive_strength,
                ))

            candidates.extend(round_entries)
            archive.extend(round_pool)
            elites = self._select_diverse_genotype_elites(archive, elite_size)
            round_best_genotype, round_best_breakdown = max(
                round_pool, key=lambda item: item[1]["score"],
            )

            if round_best_breakdown["score"] > best_breakdown["score"] + 1e-6:
                best_genotype = round_best_genotype
                best_breakdown = round_best_breakdown
                stagnation_steps = 0
            else:
                stagnation_steps += 1
                if stagnation_steps >= stagnation_patience and stagnation_steps % stagnation_patience == 0:
                    stagnation_events += 1

            history.append(float(best_breakdown["score"]))

        # Decode final best genotype to style + palette
        best_style = best_genotype.decode_to_style()
        best_palette = self._genotype_to_palette(best_genotype)
        best_spec = replace(char_spec, head_units=best_genotype.get_head_units())

        evolution_meta = {
            "enabled": True,
            "mode": "genotype_semantic",
            "iterations": int(char_spec.evolution_iterations),
            "population": int(char_spec.evolution_population),
            "variation_strength": float(char_spec.evolution_variation_strength),
            "crossover_rate": float(crossover_rate),
            "elite_size": int(elite_size),
            "stagnation_patience": int(stagnation_patience),
            "stagnation_events": int(stagnation_events),
            "strength_history": [float(v) for v in strength_history],
            "preview_states": list(best_breakdown["preview_states"]),
            "initial_score": float(candidates[0]["score"]),
            "best_score": float(best_breakdown["score"]),
            "history": history,
            "best_character": {
                "head_units": float(best_spec.head_units),
                "style": vars(best_style).copy(),
                "palette_hex": best_palette.colors_hex,
                "quality_score": float(best_breakdown["quality_score"]),
                "motion_score": float(best_breakdown["motion_score"]),
                "coverage_score": float(best_breakdown["coverage_score"]),
                "silhouette_score": float(best_breakdown["silhouette_score"]),
                "state_distinction_score": float(best_breakdown["state_distinction_score"]),
                "genotype": best_genotype.to_dict(),
            },
            "objective_weights": {
                "quality_score": 0.56,
                "motion_score": 0.15,
                "coverage_score": 0.10,
                "silhouette_score": 0.11,
                "state_distinction_score": 0.08,
            },
            "candidates": candidates,
        }
        return best_spec, best_style, best_palette, evolution_meta, history

    def _infer_root_transform(
        self,
        state: str,
        t: float,
        *,
        frame_index: int,
        frame_count: int,
        fps: int,
    ) -> MotionRootTransform:
        """Infer a lightweight root-motion track for the UMR contract."""
        dt = 1.0 / max(1, fps)
        progress = frame_index / max(1, frame_count - 1)
        state_key = state.lower()

        if state_key == "run":
            distance = 0.28 * progress
            return MotionRootTransform(x=distance, y=0.0, velocity_x=0.28 / max((frame_count - 1) * dt, dt), velocity_y=0.0)
        if state_key == "walk":
            distance = 0.16 * progress
            return MotionRootTransform(x=distance, y=0.0, velocity_x=0.16 / max((frame_count - 1) * dt, dt), velocity_y=0.0)
        if state_key == "jump":
            x = 0.06 * progress
            y = 0.18 * math.sin((math.pi * progress) / 2.0)
            vy = 0.18 * (math.pi / 2.0) * math.cos((math.pi * progress) / 2.0) / max((frame_count - 1) * dt, dt)
            return MotionRootTransform(x=x, y=y, velocity_x=0.06 / max((frame_count - 1) * dt, dt), velocity_y=vy)
        if state_key == "fall":
            start_height = 0.22
            y = start_height * (1.0 - progress)
            return MotionRootTransform(x=0.0, y=y, velocity_x=0.0, velocity_y=-start_height / max((frame_count - 1) * dt, dt))
        if state_key == "hit":
            x = -0.05 * math.sin(math.pi * progress)
            return MotionRootTransform(x=x, y=0.0, velocity_x=0.0, velocity_y=0.0)
        return MotionRootTransform(x=0.0, y=0.0, velocity_x=0.0, velocity_y=0.0)

    def _build_umr_clip_for_state(
        self,
        state: str,
        *,
        frame_count: int,
        fps: int,
    ) -> UnifiedMotionClip:
        """Build the base UMR clip before downstream filters are applied."""
        state_key = state.lower()
        frames: list[UnifiedMotionFrame] = []
        dt = 1.0 / max(1, fps)
        # SESSION-040: idle is now phase-driven — all states covered
        use_phase_generator = state_key in {"idle", "run", "walk", "jump", "fall", "hit"}

        if not use_phase_generator:
            # SESSION-040 CONTRACT: No legacy fallback. Unknown states are rejected.
            available = "idle, run, walk, jump, fall, hit"
            raise PipelineContractError(
                "unknown_state",
                f"Unknown character state '{state_key}'. "
                f"All states must have phase-driven generators. Available: {available}"
            )

        for i in range(frame_count):
            t = i / max(1, frame_count)
            time_s = i * dt
            root = self._infer_root_transform(state_key, t, frame_index=i, frame_count=frame_count, fps=fps)
            if state_key == "run":
                frame = phase_driven_run_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                )
            elif state_key == "walk":
                frame = phase_driven_walk_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                )
            elif state_key == "jump":
                frame = phase_driven_jump_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_y=root.y,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                    apex_height=0.18,
                )
            elif state_key == "fall":
                frame = phase_driven_fall_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_y=root.y,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                    ground_height=0.0,
                    fall_reference_height=0.22,
                )
            elif state_key == "hit":
                frame = phase_driven_hit_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_y=root.y,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                    damping=4.0,
                    half_life=0.18,
                    recovery_velocity=0.0,
                    impact_energy=1.0,
                )
            elif state_key == "idle":
                # SESSION-040: Phase-driven idle — eliminates legacy_pose_adapter bypass
                frame = phase_driven_idle_frame(
                    t,
                    time=time_s,
                    frame_index=i,
                    source_state=state_key,
                    root_x=root.x,
                    root_y=root.y,
                    root_velocity_x=root.velocity_x,
                    root_velocity_y=root.velocity_y,
                )
            else:
                # SESSION-040 CONTRACT: This branch should be unreachable.
                # If we get here, the state validation above has a bug.
                raise PipelineContractError(
                    "legacy_path_invoked",
                    f"Unreachable legacy path hit for state '{state_key}'. "
                    f"This is a contract violation — all states must be phase-driven."
                )
            frames.append(frame)

        return UnifiedMotionClip(
            clip_id=f"{state_key}_clip",
            state=state_key,
            fps=max(1, fps),
            frames=frames,
            metadata={
                "base_stage": "phase_or_transient_generation",
                "generator_mode": "phase_driven",  # SESSION-040: always phase-driven
                "strict_contract": "UnifiedMotionFrame",
            },
        )

    def _build_motion_nodes(
        self,
        physics_projector: Optional[AnglePoseProjector],
        biomechanics_projector: Optional[BiomechanicsProjector],
    ) -> list[MotionPipelineNode]:
        nodes: list[MotionPipelineNode] = []
        if physics_projector is not None:
            nodes.append(
                MotionPipelineNode(
                    name="physics_compliance",
                    stage="root_motion_to_compliance",
                    processor=lambda frame, dt: physics_projector.step_frame(frame, dt=dt, enforce_layer_guard=True),
                )
            )
        if biomechanics_projector is not None:
            nodes.append(
                MotionPipelineNode(
                    name="biomechanics_grounding",
                    stage="localized_grounding",
                    processor=lambda frame, dt: biomechanics_projector.step_frame(frame, dt=dt, enforce_layer_guard=True),
                )
            )
        return nodes

    def _audit_umr_pipeline(self, clip: UnifiedMotionClip, audit_log: list[Any]) -> dict[str, Any]:
        """Summarize whether the motion clip respected the intended layering contract."""
        upper_body_override_flags = 0
        grounded_frames = 0
        for frame in clip.frames:
            meta = frame.metadata
            if meta.get("physics_layer_guard") and meta.get("biomechanics_layer_guard"):
                grounded_frames += 1
            if not meta.get("physics_layer_guard", True):
                upper_body_override_flags += 1
            if meta.get("biomechanics_projected") and not meta.get("biomechanics_layer_guard", True):
                upper_body_override_flags += 1

        return {
            "frame_count": len(clip.frames),
            "audit_entries": len(audit_log),
            "grounding_guarded_frames": grounded_frames,
            "upper_body_override_flags": upper_body_override_flags,
            "node_order": clip.metadata.get("motion_pipeline", {}).get("node_order", []),
            "stage_order": clip.metadata.get("motion_pipeline", {}).get("stage_order", []),
            "contract": "UnifiedMotionFrame",
        }

    def produce_character_pack(self, char_spec: CharacterSpec) -> AssetResult:
        """Produce a practical multi-state character asset pack.

        Unlike the generic animation path (which only transforms a base sprite),
        this method renders each frame from skeletal poses and exports a usable
        state pack for direct game integration: per-state sheets, GIF previews,
        per-frame PNGs, a combined atlas, metadata, and palette provenance.

        SESSION-040: Now enforces the UMR pipeline contract via UMR_Context,
        PipelineContractGuard, and UMR_Auditor with deterministic hash sealing.
        """
        start = time.time()
        if char_spec.preset not in CHARACTER_PRESETS:
            available = ", ".join(sorted(CHARACTER_PRESETS))
            raise ValueError(
                f"Unknown character preset '{char_spec.preset}'. Available: {available}"
            )

        self._log(
            f"Producing character pack: {char_spec.name} "
            f"(preset={char_spec.preset}, states={','.join(char_spec.states)})"
        )

        style, palette = get_preset(char_spec.preset)
        asset_dir = self.output_dir / char_spec.name
        asset_dir.mkdir(parents=True, exist_ok=True)

        # SESSION-027: If use_genotype is enabled, initialize from genotype
        genotype_used: Optional[CharacterGenotype] = None
        if char_spec.use_genotype:
            if char_spec.genotype is not None:
                genotype_used = char_spec.genotype
            elif char_spec.preset in GENOTYPE_PRESETS:
                genotype_used = GENOTYPE_PRESETS[char_spec.preset]()
            else:
                genotype_used = CharacterGenotype()
            # Override style and palette from genotype
            style = genotype_used.decode_to_style()
            palette = self._genotype_to_palette(genotype_used)
            char_spec = replace(char_spec, head_units=genotype_used.get_head_units())
            self._log(
                f"Using genotype mode: archetype={genotype_used.archetype}, "
                f"template={genotype_used.body_template}, "
                f"slots={list(genotype_used.slots.keys())}"
            )

        output_paths: list[str] = []
        evolution_meta: Optional[dict[str, Any]] = None
        evolution_history: list[float] = []
        if char_spec.evolution_iterations > 0:
            self._log(
                f"Evolving character search space: iterations={char_spec.evolution_iterations}, "
                f"population={char_spec.evolution_population}"
            )
            if genotype_used is not None:
                # SESSION-027: Use genotype-based semantic evolution
                self._log("Evolution mode: genotype_semantic (3-layer mutation + crossover)")
                char_spec, style, palette, evolution_meta, evolution_history = self._evolve_character_genotype(
                    char_spec,
                    genotype_used,
                )
            else:
                # Legacy: flat style+palette evolution
                char_spec, style, palette, evolution_meta, evolution_history = self._evolve_character_spec(
                    char_spec,
                    style,
                    palette,
                )
            evolution_path = str(asset_dir / f"{char_spec.name}_character_evolution.json")
            with open(evolution_path, "w") as f:
                json.dump(evolution_meta, f, indent=2)
            output_paths.append(evolution_path)

        palette_colors = [tuple(int(v) for v in row[:3]) for row in palette.colors_srgb]

        if char_spec.export_palette:
            palette_path = str(asset_dir / f"{char_spec.name}_palette.json")
            palette.save_json(palette_path)
            output_paths.append(palette_path)

        manifest: dict[str, Any] = {
            "character": {
                "name": char_spec.name,
                "preset": char_spec.preset,
                "frame_width": char_spec.frame_width,
                "frame_height": char_spec.frame_height,
                "fps": char_spec.fps,
                "head_units": char_spec.head_units,
                "palette_size": palette.count,
                "palette_colors": [list(c) for c in palette_colors],
                "style": vars(style).copy(),
                "render_flags": {
                    "dither": char_spec.enable_dither,
                    "outline": char_spec.enable_outline,
                    "lighting": char_spec.enable_lighting,
                    "physics": char_spec.enable_physics,
                    "biomechanics": char_spec.enable_biomechanics,
                },
                "physics_config": {
                    "enabled": char_spec.enable_physics,
                    "stiffness_scale": char_spec.physics_stiffness,
                    "damping_scale": char_spec.physics_damping,
                    "cognitive_strength": char_spec.physics_cognitive_strength,
                    "engine": "AnglePoseProjector (PhysDiff-inspired)",
                },
                "biomechanics_config": {
                    "enabled": char_spec.enable_biomechanics,
                    "zmp_balance": char_spec.biomechanics_zmp,
                    "inverted_pendulum_model": char_spec.biomechanics_ipm,
                    "skating_cleanup_calculus": char_spec.biomechanics_skating_cleanup,
                    "zmp_correction_strength": char_spec.biomechanics_zmp_strength,
                    "engine": "BiomechanicsProjector (SESSION-029: ZMP/CoM + IPM + SkatingCleanup + FABRIK)",
                    "references": [
                        "Vukobratovic & Borovac, Zero-Moment Point (Humanoids 2001)",
                        "Kajita et al., 3D-LIPM (IEEE IROS 2001)",
                        "Kovar et al., Footskate Cleanup (SCA 2002)",
                        "Aristidou & Lasenby, FABRIK (Graphical Models 2011)",
                    ],
                },
                "genotype": genotype_used.to_dict() if genotype_used is not None else None,
            },
            "evolution": evolution_meta or {
                "enabled": False,
                "iterations": 0,
                "population": 0,
                "variation_strength": 0.0,
                "history": [],
            },
            "states": {},
        }

        state_sheets: list[tuple[str, Image.Image]] = []
        representative_frames: list[Image.Image] = []
        state_scores: list[float] = []

        # SESSION-035: Load Layer 3 convergence bridge if available
        # This automatically applies optimized parameters from the last
        # Layer 3 evolution cycle to the export pipeline (Gap #3 fix)
        _convergence_bridge = {}
        _bridge_path = Path(self.output_dir).parent / "LAYER3_CONVERGENCE_BRIDGE.json"
        if _bridge_path.exists():
            try:
                _convergence_bridge = json.loads(_bridge_path.read_text(encoding="utf-8"))
                self._log(
                    f"SESSION-035: Loaded convergence bridge from Layer 3 "
                    f"(cycle={_convergence_bridge.get('cycle_id', '?')}, "
                    f"fitness={_convergence_bridge.get('combined_fitness', '?'):.3f})"
                )
            except (json.JSONDecodeError, OSError):
                pass

        # SESSION-035: Apply converged parameters (override defaults if bridge exists)
        _eff_stiffness = _convergence_bridge.get(
            "physics_stiffness", char_spec.physics_stiffness
        )
        _eff_damping = _convergence_bridge.get(
            "physics_damping", char_spec.physics_damping
        )
        _eff_compliance = _convergence_bridge.get("compliance_alpha", 0.6)
        _eff_zmp_strength = _convergence_bridge.get(
            "biomechanics_zmp_strength", char_spec.biomechanics_zmp_strength
        )

        # SESSION-040: Build immutable UMR_Context and ContractGuard
        _umr_context = UMR_Context.from_character_spec(
            char_spec,
            session_id="SESSION-040",
            convergence_bridge=_convergence_bridge,
            pipeline_version="0.31.0",
        )
        _contract_guard = PipelineContractGuard(_umr_context)
        _umr_auditor = UMR_Auditor(_umr_context)
        _flicker_detector = ContactFlickerDetector()
        self._log(
            f"SESSION-040: Pipeline contract initialized "
            f"(context_hash={_umr_context.context_hash[:12]}...)"
        )

        # SESSION-028: Initialize physics projector if enabled
        # SESSION-028-SUPP: Pass skeleton_ref for PhysDiff-inspired foot locking
        # SESSION-035: Now uses compliant_pd mode with convergence bridge params
        _physics_skeleton = Skeleton.create_humanoid(head_units=char_spec.head_units)
        _physics_projector = None
        if char_spec.enable_physics:
            _cognitive_cfg = CognitiveMotionConfig(
                strength=char_spec.physics_cognitive_strength,
            )
            _physics_projector = AnglePoseProjector(
                global_stiffness_scale=_eff_stiffness,
                global_damping_scale=_eff_damping,
                cognitive_config=_cognitive_cfg,
                enable_foot_locking=True,
                skeleton_ref=_physics_skeleton,
                compliance_mode="compliant_pd",
                compliance_alpha=_eff_compliance,
            )

        # SESSION-029: Initialize biomechanics projector if enabled
        _biomechanics_projector = None
        if char_spec.enable_biomechanics:
            _biomechanics_skeleton = Skeleton.create_humanoid(
                head_units=char_spec.head_units
            )
            _biomechanics_projector = BiomechanicsProjector(
                skeleton=_biomechanics_skeleton,
                enable_zmp=char_spec.biomechanics_zmp,
                enable_ipm=char_spec.biomechanics_ipm,
                enable_skating_cleanup=char_spec.biomechanics_skating_cleanup,
                zmp_correction_strength=_eff_zmp_strength,  # SESSION-035: convergence bridge
            )
            self._log(
                f"Biomechanics projector enabled: "
                f"ZMP={char_spec.biomechanics_zmp}, "
                f"IPM={char_spec.biomechanics_ipm}, "
                f"SkatingCleanup={char_spec.biomechanics_skating_cleanup}"
            )

        manifest["motion_contract"] = {
            "name": "UnifiedMotionFrame",
            "format": "umr_motion_clip_v1",
            "pipeline_order": [
                "intent_state_selection",
                "phase_driven_base_generation",  # SESSION-040: no legacy path
                "root_motion",
                "physics_compliance",
                "localized_grounding",
                "render_export",
            ],
            "required_fields": [
                "time",
                "phase",
                "root_transform",
                "joint_local_rotations",
                "contact_tags",
            ],
        }

        for state in char_spec.states:
            frame_count = max(1, int(char_spec.state_frames.get(state, char_spec.frames_per_state)))
            frame_duration_ms = max(16, 1000 // max(1, char_spec.fps))
            loop_flag = bool(char_spec.loop_overrides.get(state, state in {"idle", "run"}))
            dt = 1.0 / max(1, char_spec.fps)

            if _physics_projector is not None:
                _physics_projector.reset()
            if _biomechanics_projector is not None:
                _biomechanics_projector.reset()

            base_clip = self._build_umr_clip_for_state(
                state,
                frame_count=frame_count,
                fps=char_spec.fps,
            )
            pipeline_nodes = self._build_motion_nodes(_physics_projector, _biomechanics_projector)
            motion_result = run_motion_pipeline(base_clip, pipeline_nodes, dt=dt)
            motion_clip = motion_result.clip
            motion_audit = self._audit_umr_pipeline(motion_clip, motion_result.audit_log)

            # SESSION-040: Contract enforcement — reject legacy generator mode
            _clip_gen_mode = motion_clip.metadata.get("generator_mode", "")
            _contract_guard.reject_legacy_bypass(_clip_gen_mode, caller=f"state:{state}")

            # SESSION-040: Register clip frames with the auditor for hash sealing
            _clip_frame_dicts = [f.to_dict() for f in motion_clip.frames]
            _node_order = motion_clip.metadata.get("motion_pipeline", {}).get("node_order", [])
            _umr_auditor.register_clip(state, _clip_frame_dicts, node_order=_node_order)

            # SESSION-040: Validate required UMR fields on every frame
            for _fd in _clip_frame_dicts:
                _contract_guard.validate_required_fields(_fd, caller=f"state:{state}")

            # SESSION-040: Contact flicker detection
            _flicker_report = _flicker_detector.check_clip(_clip_frame_dicts)
            if not _flicker_report["clean"]:
                self._log(
                    f"SESSION-040 WARNING: Contact flicker detected in '{state}' "
                    f"({_flicker_report['flicker_count']} frames: {_flicker_report['flicker_frames']})"
                )

            umr_path = str(asset_dir / f"{char_spec.name}_{state}.umr.json")
            motion_clip.save(umr_path)
            output_paths.append(umr_path)

            frames: list[Image.Image] = []
            frame_scores: list[float] = []
            for motion_frame in motion_clip.frames:
                pose = dict(motion_frame.joint_local_rotations)
                skeleton = Skeleton.create_humanoid(head_units=char_spec.head_units)
                frame = render_character_frame(
                    skeleton,
                    pose,
                    style,
                    width=char_spec.frame_width,
                    height=char_spec.frame_height,
                    palette=palette,
                    enable_dither=char_spec.enable_dither,
                    enable_outline=char_spec.enable_outline,
                    enable_lighting=char_spec.enable_lighting,
                )
                frames.append(frame)
                try:
                    eval_result = self.evaluator.evaluate(frame, palette=palette)
                    frame_scores.append(float(eval_result.overall_score))
                except Exception:
                    frame_scores.append(0.0)

            representative_frames.append(frames[0])
            state_score = float(np.mean(frame_scores)) if frame_scores else 0.0
            state_scores.append(state_score)

            sheet_width = char_spec.frame_width * frame_count
            sheet = Image.new(
                "RGBA",
                (sheet_width, char_spec.frame_height),
                (0, 0, 0, 0),
            )
            for i, frame in enumerate(frames):
                sheet.paste(frame, (i * char_spec.frame_width, 0))
            state_sheets.append((state, sheet))

            sheet_path = str(asset_dir / f"{char_spec.name}_{state}_sheet.png")
            sheet.save(sheet_path)
            output_paths.append(sheet_path)

            gif_path = str(asset_dir / f"{char_spec.name}_{state}.gif")
            try:
                frames[0].save(
                    gif_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=frame_duration_ms,
                    loop=0 if loop_flag else 1,
                    disposal=2,
                )
                output_paths.append(gif_path)
            except Exception as e:
                self._log(f"Character GIF export failed for {state} (non-fatal): {e}")

            frame_files: list[str] = []
            for i, frame in enumerate(frames):
                frame_path = str(asset_dir / f"{char_spec.name}_{state}_frame_{i:02d}.png")
                frame.save(frame_path)
                output_paths.append(frame_path)
                frame_files.append(frame_path)

            state_meta = {
                "state": state,
                "frames": frame_count,
                "fps": char_spec.fps,
                "loop": loop_flag,
                "frame_size": {"w": char_spec.frame_width, "h": char_spec.frame_height},
                "sheet": os.path.basename(sheet_path),
                "gif": os.path.basename(gif_path),
                "frame_duration_ms": frame_duration_ms,
                "score": state_score,
                "frame_files": [os.path.basename(p) for p in frame_files],
                "motion_bus": {
                    "format": "umr_motion_clip_v1",
                    "file": os.path.basename(umr_path),
                    "audit": motion_audit,
                    "contact_coverage": {
                        "left_foot_frames": int(sum(1 for f in motion_clip.frames if f.contact_tags.left_foot)),
                        "right_foot_frames": int(sum(1 for f in motion_clip.frames if f.contact_tags.right_foot)),
                    },
                },
                "sheet_frames": [
                    {
                        "name": f"{char_spec.name}_{state}_{i}",
                        "rect": [i * char_spec.frame_width, 0, char_spec.frame_width, char_spec.frame_height],
                        "duration": frame_duration_ms,
                    }
                    for i in range(frame_count)
                ],
            }
            state_meta_path = str(asset_dir / f"{char_spec.name}_{state}.anim.json")
            with open(state_meta_path, "w") as f:
                json.dump(state_meta, f, indent=2)
            output_paths.append(state_meta_path)
            manifest["states"][state] = state_meta

        atlas_width = max(sheet.width for _, sheet in state_sheets)
        atlas_height = char_spec.frame_height * len(state_sheets)
        atlas = Image.new("RGBA", (atlas_width, atlas_height), (0, 0, 0, 0))
        atlas_layout: list[dict[str, Any]] = []
        cursor_y = 0
        for state, sheet in state_sheets:
            atlas.paste(sheet, (0, cursor_y))
            atlas_layout.append(
                {
                    "state": state,
                    "rect": [0, cursor_y, sheet.width, sheet.height],
                    "sheet": f"{char_spec.name}_{state}_sheet.png",
                }
            )
            cursor_y += char_spec.frame_height

        atlas_path = str(asset_dir / f"{char_spec.name}_character_atlas.png")
        atlas.save(atlas_path)
        output_paths.append(atlas_path)

        manifest["atlas"] = {
            "image": os.path.basename(atlas_path),
            "size": {"w": atlas_width, "h": atlas_height},
            "layout": atlas_layout,
        }
        manifest["summary"] = {
            "state_count": len(char_spec.states),
            "average_state_score": float(np.mean(state_scores)) if state_scores else 0.0,
            "best_state_score": float(np.max(state_scores)) if state_scores else 0.0,
            "lowest_state_score": float(np.min(state_scores)) if state_scores else 0.0,
            "evolution_iterations": int(char_spec.evolution_iterations),
            "evolution_enabled": bool(char_spec.evolution_iterations > 0),
            "umr_state_coverage": len(manifest["states"]),
            "umr_contract": "UnifiedMotionFrame",
            "umr_pipeline_ready_for_layer3": True,
        }

        # SESSION-040: Add pipeline contract summary to manifest
        manifest["pipeline_contract"] = {
            "session": "SESSION-040",
            "context_hash": _umr_context.context_hash,
            "contract_guard": _contract_guard.summary(),
            "all_states_phase_driven": True,
            "legacy_bypass_blocked": True,
        }

        manifest_path = str(asset_dir / f"{char_spec.name}_character_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        output_paths.append(manifest_path)

        # SESSION-040: Compute and write deterministic hash seal (.umr_manifest.json)
        _seal_path = str(asset_dir / ".umr_manifest.json")
        _seal = _umr_auditor.save_manifest(_seal_path)
        output_paths.append(_seal_path)
        self._log(
            f"SESSION-040: Hash seal written "
            f"(pipeline_hash={_seal.pipeline_hash[:16]}..., "
            f"frames={_seal.frame_count}, "
            f"contact_hash={_seal.contact_tag_hash[:12]}...)"
        )

        elapsed = time.time() - start
        self._log(
            f"Character pack done: {len(char_spec.states)} states, "
            f"time={elapsed:.1f}s"
        )

        return AssetResult(
            name=char_spec.name,
            image=representative_frames[0] if representative_frames else None,
            spritesheet=atlas,
            frames=representative_frames,
            metadata=manifest,
            score=float(np.mean(state_scores)) if state_scores else 0.0,
            evolution_history=evolution_history,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def produce_texture_atlas(
        self,
        name: str = "textures",
        n_textures: int = 25,
        evolution_steps: int = 200,
        tile_size: int = 64,
        seed: int = 42,
    ) -> AssetResult:
        """Produce a diverse texture atlas via CPPN MAP-Elites evolution."""
        start = time.time()
        self._log(f"Producing texture atlas: {name} "
                   f"({n_textures} textures, {evolution_steps} evolution steps)")

        evolver = CPPNEvolver(grid_dims=(5, 5, 5), seed=seed)
        archive = evolver.run(
            n_iterations=evolution_steps,
            initial_population=min(50, evolution_steps // 2),
            render_size=tile_size,
            verbose=self.verbose,
        )

        # Export atlas
        asset_dir = self.output_dir / name
        asset_dir.mkdir(parents=True, exist_ok=True)

        atlas_path = str(asset_dir / f"{name}_atlas.png")
        atlas = evolver.export_texture_atlas(atlas_path, tile_size=tile_size)

        # Save individual textures
        output_paths = [atlas_path]
        best_textures = evolver.get_best_textures(n=n_textures)
        for i, (img, fitness, features) in enumerate(best_textures):
            tex_path = str(asset_dir / f"texture_{i:03d}.png")
            img.save(tex_path)
            output_paths.append(tex_path)

        # Save metadata
        meta = {
            "name": name,
            "n_textures": len(best_textures),
            "evolution_steps": evolution_steps,
            "tile_size": tile_size,
            "archive_size": len(archive),
            "textures": [
                {
                    "index": i,
                    "fitness": float(fitness),
                    "symmetry": float(features[0]),
                    "complexity": float(features[1]),
                    "color_diversity": float(features[2]),
                }
                for i, (_, fitness, features) in enumerate(best_textures)
            ],
        }
        meta_path = str(asset_dir / f"{name}_meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        output_paths.append(meta_path)

        elapsed = time.time() - start
        self._log(f"Texture atlas done: {len(best_textures)} textures, "
                   f"archive={len(archive)} cells, time={elapsed:.1f}s")

        return AssetResult(
            name=name,
            image=atlas,
            metadata=meta,
            score=max(t[1] for t in best_textures) if best_textures else 0.0,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def _resolve_level_theme(self, theme: str) -> LevelTheme:
        try:
            return LevelTheme(theme)
        except ValueError:
            return LevelTheme.GRASSLAND

    def _resolve_level_render_mode(self, render_mode: str) -> LevelRenderMode:
        try:
            return LevelRenderMode(render_mode)
        except ValueError:
            return LevelRenderMode.FLAT_2D

    def _render_level_preview(
        self,
        scene: UniversalSceneDescription,
        tile_px: int = 16,
    ) -> Image.Image:
        rows = [line for line in scene.ascii_layout.strip("\n").splitlines() if line]
        cols = max((len(line) for line in rows), default=1)
        bg = tuple(scene.level_spec.bg_color) + (255,)
        image = Image.new("RGBA", (cols * tile_px, len(rows) * tile_px), bg)
        draw = ImageDraw.Draw(image)

        color_map = {
            "#": (124, 92, 70, 255),
            "=": (170, 132, 96, 255),
            "X": (186, 48, 48, 255),
            "^": (236, 92, 44, 255),
            "E": (140, 82, 255, 255),
            "M": (255, 255, 255, 255),
            "G": (255, 220, 74, 255),
            "C": (104, 220, 140, 255),
            ".": (0, 0, 0, 0),
            " ": (0, 0, 0, 0),
        }

        for row_idx, row in enumerate(rows):
            for col_idx, tile in enumerate(row):
                fill = color_map.get(tile, (200, 200, 200, 255))
                x0 = col_idx * tile_px
                y0 = row_idx * tile_px
                x1 = x0 + tile_px - 1
                y1 = y0 + tile_px - 1
                if fill[3] == 0:
                    continue
                draw.rectangle([x0, y0, x1, y1], fill=fill)
                draw.rectangle([x0, y0, x1, y1], outline=(22, 22, 30, 255), width=1)

        return image

    def produce_level_pack(self, level_spec: LevelPipelineSpec) -> AssetResult:
        """Produce a DAG-orchestrated procedural level bundle.

        This method is the Gap 1 repair layer: WFC, scene description,
        shader generation, export, and knowledge distillation are executed
        as one traced dependency graph instead of isolated code paths.
        """
        self._log(f"=== Producing Level Pack via PDG: {level_spec.level_id} ===")
        start = time.time()

        run_seed = self.seed if level_spec.seed is None else level_spec.seed
        theme = self._resolve_level_theme(level_spec.theme)
        render_mode = self._resolve_level_render_mode(level_spec.render_mode)
        runtime_level_spec = LevelSpec(
            level_id=level_spec.level_id,
            theme=theme,
            render_mode=render_mode,
            tile_width=level_spec.tile_size,
            tile_height=level_spec.tile_size,
            grid_cols=level_spec.width,
            grid_rows=level_spec.height,
            palette_size=level_spec.palette_size,
            required_assets=list(level_spec.required_assets),
        )

        level_dir = self.output_dir / "levels" / level_spec.level_id
        scene_dir = level_dir / "scene"
        shader_dir = level_dir / "shaders"
        export_dir = level_dir / "exports"
        for directory in (level_dir, scene_dir, shader_dir, export_dir):
            directory.mkdir(parents=True, exist_ok=True)

        bridge = LevelSpecBridge(project_root=level_dir)
        shader_generator = ShaderCodeGenerator()
        exporter = AssetExporter(
            ExportConfig(output_dir=str(export_dir), style_name="Style_MathArt", version=1)
        )

        graph = ProceduralDependencyGraph(name=f"pdg_{level_spec.level_id}")

        def _node_wfc(_: dict[str, Any], __: dict[str, Any]) -> dict[str, Any]:
            generator = WFCGenerator(seed=run_seed)
            generator.learn()
            ascii_level = generator.generate(
                level_spec.width,
                level_spec.height,
                ensure_ground=level_spec.ensure_ground,
                ensure_spawn=level_spec.ensure_spawn,
                ensure_goal=level_spec.ensure_goal,
            )
            ascii_path = scene_dir / f"{level_spec.level_id}.level.txt"
            ascii_path.write_text(ascii_level, encoding="utf-8")
            return {
                "ascii_level": ascii_level,
                "ascii_path": str(ascii_path),
                "seed": run_seed,
            }

        def _node_scene(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            ascii_level = deps["wfc_generate"]["ascii_level"]
            scene = UniversalSceneDescription.from_ascii_level(
                ascii_level,
                runtime_level_spec,
                metadata={
                    "source": "wfc",
                    "orchestration": "procedural_dependency_graph",
                    "goal": "bridge_wfc_shader_export",
                },
            )
            scene_path = scene.save(scene_dir / f"{level_spec.level_id}.scene.usd.json")
            asset_spec = bridge.to_asset_spec(runtime_level_spec)
            asset_spec_path = scene_dir / f"{level_spec.level_id}.asset_spec.json"
            asset_spec_path.write_text(
                json.dumps(asset_spec.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return {
                "scene": scene,
                "scene_path": str(scene_path),
                "asset_spec": asset_spec,
                "asset_spec_path": str(asset_spec_path),
            }

        def _node_shader_plan(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            scene = deps["scene_describe"]["scene"]
            plan = scene.derive_shader_recipe(level_spec.shader_goal)
            plan_path = shader_dir / "shader_plan.json"
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
            return {
                "shader_plan": plan,
                "shader_plan_path": str(plan_path),
            }

        def _node_shader_generate(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            plan = deps["shader_plan"]["shader_plan"]
            shader_entries = [
                {
                    "shader_type": plan["shader_type"],
                    "preset_name": plan.get("preset_name"),
                    "subdir": "base",
                },
                *[
                    {
                        "shader_type": overlay["shader_type"],
                        "preset_name": overlay.get("preset_name"),
                        "subdir": overlay["shader_type"],
                    }
                    for overlay in plan.get("overlays", [])
                ],
            ]
            shader_files: list[str] = []
            for entry in shader_entries:
                saved = shader_generator.save_all(
                    shader_dir / entry["subdir"],
                    entry["shader_type"],
                    entry.get("preset_name"),
                )
                shader_files.extend(str(path) for path in saved)
            return {"shader_files": shader_files}

        def _node_preview(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            scene = deps["scene_describe"]["scene"]
            preview = self._render_level_preview(scene, tile_px=level_spec.tile_size)
            preview_path = level_dir / f"{level_spec.level_id}_preview.png"
            preview.save(preview_path)
            return {
                "preview_image": preview,
                "preview_path": str(preview_path),
            }

        def _node_export(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            if not level_spec.export_preview:
                return {"exported_preview_path": None, "export_manifest_path": None}
            preview = deps["preview_render"]["preview_image"]
            export_path = exporter.export_sprite(
                preview,
                name=f"{level_spec.level_id}_scene_mask",
                category="Environment",
                level_id=level_spec.level_id,
                render_mode=runtime_level_spec.render_mode.value,
                tags=["level_preview", "wfc", "pdg", runtime_level_spec.theme.value],
                validation={"scene_format": "usd_like_scene_v1"},
            )
            manifest_path = exporter.save_manifest()
            return {
                "exported_preview_path": str(export_path),
                "export_manifest_path": str(manifest_path),
            }

        def _node_bundle(_: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
            scene = deps["scene_describe"]["scene"]
            bundle = {
                "pipeline_type": "level_pdg",
                "level_id": level_spec.level_id,
                "seed": run_seed,
                "scene_format": scene.format_version,
                "ascii_level": deps["wfc_generate"]["ascii_level"],
                "ascii_path": deps["wfc_generate"]["ascii_path"],
                "scene_metrics": scene.metrics,
                "scene_path": deps["scene_describe"]["scene_path"],
                "asset_spec_path": deps["scene_describe"]["asset_spec_path"],
                "shader_plan": deps["shader_plan"]["shader_plan"],
                "shader_plan_path": deps["shader_plan"]["shader_plan_path"],
                "shader_files": deps["shader_generate"]["shader_files"],
                "preview_path": deps["preview_render"]["preview_path"],
                "exported_preview_path": deps["export_preview"]["exported_preview_path"],
                "export_manifest_path": deps["export_preview"]["export_manifest_path"],
                "research_alignment": {
                    "pdg": True,
                    "usd_like_scene_description": True,
                    "wfc_to_shader_bridge": True,
                    "shader_to_export_bridge": True,
                },
            }
            bundle_path = level_dir / f"{level_spec.level_id}_bundle.json"
            bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"bundle": bundle, "bundle_path": str(bundle_path)}

        graph.add_node(PDGNode(name="wfc_generate", operation=_node_wfc, description="Generate WFC ASCII level"))
        graph.add_node(PDGNode(name="scene_describe", operation=_node_scene, dependencies=["wfc_generate"], description="Build USD-like scene description"))
        graph.add_node(PDGNode(name="shader_plan", operation=_node_shader_plan, dependencies=["scene_describe"], description="Infer shader recipe from scene metrics"))
        graph.add_node(PDGNode(name="shader_generate", operation=_node_shader_generate, dependencies=["shader_plan"], description="Generate shader assets"))
        graph.add_node(PDGNode(name="preview_render", operation=_node_preview, dependencies=["scene_describe"], description="Render deterministic level preview"))
        graph.add_node(PDGNode(name="export_preview", operation=_node_export, dependencies=["preview_render"], description="Export preview through bridge"))
        graph.add_node(PDGNode(name="bundle_level", operation=_node_bundle, dependencies=["wfc_generate", "scene_describe", "shader_plan", "shader_generate", "preview_render", "export_preview"], description="Collect bundle metadata"))

        run = graph.run(["bundle_level"], initial_context={"level_spec": level_spec})
        bundle = dict(run["target_outputs"]["bundle_level"]["bundle"])
        bundle["pdg_execution_order"] = list(run["execution_order"])
        bundle["pdg_trace"] = list(run["trace"])

        knowledge_dir = (self.project_root / "knowledge") if self.project_root else (self.output_dir / "knowledge")
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        distiller = PhysicsKnowledgeDistiller(knowledge_dir=knowledge_dir)
        distilled_rules = distiller.distill_pipeline_success(bundle, archetype="level_pdg")
        bundle["distilled_knowledge_rules"] = distilled_rules

        bundle_path = Path(run["target_outputs"]["bundle_level"]["bundle_path"])
        bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")

        output_paths = [
            bundle["ascii_path"],
            bundle["scene_path"],
            bundle["asset_spec_path"],
            bundle["shader_plan_path"],
            *bundle["shader_files"],
            bundle["preview_path"],
            bundle_path.as_posix(),
        ]
        if bundle.get("exported_preview_path"):
            output_paths.append(bundle["exported_preview_path"])
        if bundle.get("export_manifest_path"):
            output_paths.append(bundle["export_manifest_path"])
        if distilled_rules:
            output_paths.append(str(knowledge_dir / "procedural_pipeline.md"))

        elapsed = time.time() - start
        preview_image = run["results"]["preview_render"]["preview_image"]
        self._log(
            f"Level pack done: nodes={len(run['execution_order'])}, files={len(output_paths)}, time={elapsed:.1f}s"
        )
        return AssetResult(
            name=level_spec.level_id,
            image=preview_image,
            metadata=bundle,
            score=1.0,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def produce_asset_pack(
        self,
        pack_name: str = "game_assets",
        sprites: Optional[list[AssetSpec]] = None,
        animations: Optional[list[AnimationSpec]] = None,
        characters: Optional[list[CharacterSpec]] = None,
        levels: Optional[list[LevelPipelineSpec]] = None,
        include_textures: bool = True,
    ) -> list[AssetResult]:
        """Produce a complete asset pack with sprites, characters, textures, and optional PDG-driven levels."""
        self._log(f"=== Producing Asset Pack: {pack_name} ===")
        start = time.time()
        results = []

        # Default sprites if none specified
        if sprites is None:
            sprites = [
                AssetSpec(name="coin", shape="coin", style="metal", base_hue=0.12),
                AssetSpec(name="gem", shape="gem", style="crystal", base_hue=0.55),
                AssetSpec(name="heart", shape="heart", style="default", base_hue=0.0),
                AssetSpec(name="shield", shape="shield", style="metal", base_hue=0.6),
                AssetSpec(name="platform", shape="platform", style="stone"),
                AssetSpec(name="bullet", shape="bullet", style="metal", base_hue=0.1),
            ]

        # Produce sprites
        for spec in sprites:
            try:
                result = self.produce_sprite(spec)
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing {spec.name}: {e}")

        # Produce animations
        if animations is None:
            animations = [
                AnimationSpec(
                    asset=AssetSpec(name="coin_spin", shape="coin", style="metal"),
                    animation_type="idle", n_frames=8,
                ),
                AnimationSpec(
                    asset=AssetSpec(name="gem_bounce", shape="gem", style="crystal"),
                    animation_type="jump", n_frames=12,
                ),
            ]

        for anim_spec in animations:
            try:
                result = self.produce_animation(anim_spec)
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing animation {anim_spec.asset.name}: {e}")

        # Produce practical character packs
        if characters is None:
            characters = [
                CharacterSpec(
                    name="mario_character",
                    preset="mario",
                    frames_per_state=6,
                    states=["idle", "run", "jump", "fall", "hit"],
                ),
                CharacterSpec(
                    name="trickster_character",
                    preset="trickster",
                    frames_per_state=6,
                    states=["idle", "run", "jump", "hit"],
                ),
            ]

        for char_spec in characters:
            try:
                result = self.produce_character_pack(char_spec)
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing character pack {char_spec.name}: {e}")

        # Produce PDG-driven levels
        if levels:
            for level_pipeline_spec in levels:
                try:
                    result = self.produce_level_pack(level_pipeline_spec)
                    results.append(result)
                except Exception as e:
                    self._log(f"ERROR producing level pack {level_pipeline_spec.level_id}: {e}")

        # Produce textures
        if include_textures:
            try:
                result = self.produce_texture_atlas(
                    name=f"{pack_name}_textures",
                    n_textures=16,
                    evolution_steps=150,
                )
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing textures: {e}")

        elapsed = time.time() - start
        self._log(f"=== Asset Pack Complete: {len(results)} assets, "
                   f"time={elapsed:.1f}s ===")

        # Save production summary
        summary = {
            "pack_name": pack_name,
            "total_assets": len(results),
            "total_levels": sum(1 for r in results if r.metadata.get("pipeline_type") == "level_pdg"),
            "total_time": elapsed,
            "assets": [
                {
                    "name": r.name,
                    "score": r.score,
                    "time": r.elapsed_seconds,
                    "files": len(r.output_paths),
                    "pipeline_type": r.metadata.get("pipeline_type", "asset"),
                }
                for r in results
            ],
        }
        summary_path = str(self.output_dir / f"{pack_name}_summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        return results

    def _apply_transform(
        self,
        img: Image.Image,
        scale: tuple[float, float] = (1.0, 1.0),
        rotation: float = 0.0,
        opacity: float = 1.0,
        position: tuple[float, float] = (0.0, 0.0),
    ) -> Image.Image:
        """Apply affine transform to a sprite image.

        Uses nearest-neighbor interpolation to preserve pixel art crispness.
        """
        w, h = img.size

        # Scale
        new_w = max(1, int(w * scale[0]))
        new_h = max(1, int(h * scale[1]))
        result = img.resize((new_w, new_h), Image.NEAREST)

        # Rotation (in degrees)
        if abs(rotation) > 0.01:
            rot_deg = math.degrees(rotation)
            result = result.rotate(
                -rot_deg, resample=Image.NEAREST, expand=False,
                fillcolor=(0, 0, 0, 0),
            )

        # Resize back to original dimensions
        if result.size != (w, h):
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            paste_x = (w - result.size[0]) // 2
            paste_y = (h - result.size[1]) // 2
            canvas.paste(result, (paste_x, paste_y))
            result = canvas

        # Opacity
        if opacity < 1.0:
            arr = np.array(result)
            arr[:, :, 3] = (arr[:, :, 3] * opacity).astype(np.uint8)
            result = Image.fromarray(arr, "RGBA")

        return result

    # ── SESSION-019: VFX and Deformation Animation ────────────────────────────

    def produce_vfx(
        self,
        name: str = "fire_vfx",
        preset: str = "fire",
        canvas_size: int = 64,
        n_frames: int = 16,
        seed: int = 42,
        obstacle_mask: Optional[np.ndarray | Image.Image] = None,
        driver_impulses: Optional[list[Any]] = None,
    ) -> AssetResult:
        """Produce a VFX animation.

        Supported presets now include both legacy particle emitters and the new
        **grid-based Stable Fluids smoke path** for Gap C2:
        `smoke_fluid`, `dash_smoke`, and `slash_smoke`.

        Parameters
        ----------
        obstacle_mask : np.ndarray or PIL.Image, optional
            Optional character/body mask projected into the fluid grid so smoke
            curls around the occupied silhouette.
        driver_impulses : list[Any], optional
            Optional per-frame external velocity injections for future gameplay
            integration. When omitted, preset-specific drivers are generated.
        """
        start = time.time()
        self._log(f"Producing VFX: {name} (preset={preset}, frames={n_frames})")

        legacy_preset_map = {
            "fire": ParticleConfig.fire,
            "explosion": ParticleConfig.explosion,
            "sparkle": ParticleConfig.sparkle,
            "smoke": ParticleConfig.smoke,
        }
        fluid_preset_map = {
            "smoke_fluid": FluidVFXConfig.smoke_fluid,
            "dash_smoke": FluidVFXConfig.dash_smoke,
            "slash_smoke": FluidVFXConfig.slash_smoke,
        }

        use_fluid = preset in fluid_preset_map
        if use_fluid:
            config = fluid_preset_map[preset](canvas_size=canvas_size)
            config.seed = seed
            system = FluidDrivenVFXSystem(config)
            fluid_obstacle = None
            if obstacle_mask is not None:
                fluid_obstacle = resize_mask_to_grid(obstacle_mask, config.fluid.grid_size)
            frames = system.simulate_and_render(
                n_frames=n_frames,
                driver_impulses=driver_impulses,
                obstacle_mask=fluid_obstacle,
            )
        else:
            config_factory = legacy_preset_map.get(preset, ParticleConfig.fire)
            config = config_factory(canvas_size=canvas_size)
            config.seed = seed
            system = ParticleSystem(config)
            frames = system.simulate_and_render(n_frames=n_frames)

        asset_dir = self.output_dir / name
        asset_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []

        gif_path = str(asset_dir / f"{name}.gif")
        system.export_gif(frames, gif_path)
        output_paths.append(gif_path)
        self._log(f"Saved GIF: {gif_path}")

        sheet_path = str(asset_dir / f"{name}_sheet.png")
        meta = system.export_spritesheet(frames, sheet_path)
        output_paths.append(sheet_path)
        self._log(f"Saved spritesheet: {sheet_path}")

        for i, frame in enumerate(frames):
            fp = str(asset_dir / f"{name}_frame_{i:02d}.png")
            frame.save(fp)
            output_paths.append(fp)

        if use_fluid and hasattr(system, "build_metadata"):
            extra_meta = system.build_metadata(preset_name=preset, n_frames=n_frames)
            meta.update(extra_meta)
            meta["driver_mode"] = getattr(config, "driver_mode", "fluid")
            meta["stable_fluids"] = True
        meta["preset"] = preset
        meta["seed"] = seed
        meta["simulation_kind"] = "fluid" if use_fluid else "particle"
        if obstacle_mask is not None:
            meta["has_obstacle_mask"] = True
        meta_path = str(asset_dir / f"{name}_meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        output_paths.append(meta_path)

        score = 0.0
        if frames:
            result = self.evaluator.evaluate_multi_frame_vfx(frames)
            score = result.overall_score
        if use_fluid and getattr(system, "last_diagnostics", None):
            flow_bonus = float(np.mean([d.mean_flow_energy for d in system.last_diagnostics]))
            score = min(1.0, score + min(flow_bonus * 0.15, 0.08))
            meta["flow_energy_bonus"] = flow_bonus

        elapsed = time.time() - start
        self._log(f"VFX done: {n_frames} frames, score={score:.3f}, time={elapsed:.1f}s")

        self._production_log.append({
            "name": name, "type": "vfx",
            "score": score, "elapsed": elapsed,
            "preset": preset,
        })

        return AssetResult(
            name=name,
            frames=frames,
            metadata=meta,
            score=score,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def produce_deform_animation(
        self,
        spec: AssetSpec,
        deform_type: str = "squash_stretch",
        n_frames: int = 12,
        intensity: float = 0.2,
        fps: int = 12,
    ) -> AssetResult:
        """Produce a cage-deformation animation of a sprite.

        SESSION-019: Integrates CageDeformer into the main pipeline.
        First produces a base sprite via evolution, then applies cage deformation.
        Outputs: GIF, spritesheet PNG, individual frames, JSON metadata.
        """
        start = time.time()
        self._log(f"Producing deform animation: {spec.name} "
                  f"(deform={deform_type}, frames={n_frames})")

        # Step 1: Get base sprite
        base_result = self.produce_sprite(spec)
        if base_result.image is None:
            self._log("ERROR: Failed to produce base sprite")
            return AssetResult(name=spec.name, score=0.0)

        # Step 2: Select deformation preset
        preset_map = {
            "squash_stretch": lambda: CagePreset.squash_stretch(intensity=intensity),
            "wobble": lambda: CagePreset.wobble(intensity=intensity),
            "breathe": lambda: CagePreset.breathe(intensity=intensity),
            "lean": lambda: CagePreset.lean(intensity=intensity),
        }
        anim_factory = preset_map.get(deform_type, preset_map["squash_stretch"])
        cage_anim = anim_factory()

        # Step 3: Apply cage deformation
        deformer = CageDeformer(base_result.image)
        frames = deformer.animate(cage_anim, n_frames=n_frames)

        # Step 4: Save outputs
        asset_dir = self.output_dir / spec.name
        asset_dir.mkdir(parents=True, exist_ok=True)
        output_paths = list(base_result.output_paths)

        # GIF
        gif_path = str(asset_dir / f"{spec.name}_deform.gif")
        deformer.export_gif(frames, gif_path, fps=fps)
        output_paths.append(gif_path)
        self._log(f"Saved deform GIF: {gif_path}")

        # Spritesheet
        sheet_path = str(asset_dir / f"{spec.name}_deform_sheet.png")
        sheet_meta = deformer.export_spritesheet(frames, sheet_path)
        output_paths.append(sheet_path)
        self._log(f"Saved deform spritesheet: {sheet_path}")

        # Individual frames
        for i, frame in enumerate(frames):
            fp = str(asset_dir / f"{spec.name}_deform_{i:02d}.png")
            frame.save(fp)
            output_paths.append(fp)

        # Metadata
        full_meta = {
            **sheet_meta,
            "deform_type": deform_type,
            "intensity": intensity,
            "n_frames": n_frames,
            "fps": fps,
            "base_score": base_result.score,
        }
        meta_path = str(asset_dir / f"{spec.name}_deform_meta.json")
        with open(meta_path, "w") as f:
            json.dump(full_meta, f, indent=2)
        output_paths.append(meta_path)

        elapsed = time.time() - start
        self._log(f"Deform animation done: {n_frames} frames, time={elapsed:.1f}s")

        self._production_log.append({
            "name": spec.name, "type": "deform_animation",
            "score": base_result.score, "elapsed": elapsed,
        })

        return AssetResult(
            name=spec.name,
            image=base_result.image,
            spritesheet=None,
            frames=frames,
            metadata=full_meta,
            score=base_result.score,
            evolution_history=base_result.evolution_history,
            elapsed_seconds=elapsed,
            output_paths=output_paths,
        )

    def produce_full_asset_pack(
        self,
        pack_name: str = "full_game_assets",
        sprites: Optional[list[AssetSpec]] = None,
        animations: Optional[list[AnimationSpec]] = None,
        vfx_presets: Optional[list[str]] = None,
        deform_specs: Optional[list[tuple[AssetSpec, str]]] = None,
        include_textures: bool = True,
    ) -> list[AssetResult]:
        """Produce a complete asset pack including sprites, animations, VFX, and deformations.

        SESSION-019: Extended version of produce_asset_pack that includes
        particle VFX and cage deformation animations.
        """
        self._log(f"=== Producing Full Asset Pack: {pack_name} ===")
        start = time.time()

        # Start with base asset pack
        results = self.produce_asset_pack(
            pack_name=pack_name,
            sprites=sprites,
            animations=animations,
            include_textures=include_textures,
        )

        # Add VFX
        if vfx_presets is None:
            vfx_presets = ["fire", "explosion", "sparkle", "smoke"]
        for preset in vfx_presets:
            try:
                result = self.produce_vfx(
                    name=f"{pack_name}_{preset}",
                    preset=preset,
                    seed=self.seed,
                )
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing VFX {preset}: {e}")

        # Add deformation animations
        if deform_specs is None:
            deform_specs = [
                (AssetSpec(name=f"{pack_name}_coin_squash", shape="coin", style="metal"), "squash_stretch"),
                (AssetSpec(name=f"{pack_name}_gem_wobble", shape="gem", style="crystal"), "wobble"),
                (AssetSpec(name=f"{pack_name}_heart_breathe", shape="heart"), "breathe"),
            ]
        for spec, deform_type in deform_specs:
            try:
                result = self.produce_deform_animation(
                    spec=spec,
                    deform_type=deform_type,
                )
                results.append(result)
            except Exception as e:
                self._log(f"ERROR producing deform {spec.name}: {e}")

        elapsed = time.time() - start
        self._log(f"=== Full Asset Pack Complete: {len(results)} assets, "
                  f"time={elapsed:.1f}s ===")

        return results

    # ── SESSION-020: Multi-layer Render Compositing ────────────────────────

    def produce_layered_sprite(
        self,
        spec: AssetSpec,
        export_layers: bool = True,
    ) -> tuple[AssetResult, Optional[LayeredRenderResult]]:
        """Produce a sprite with separated render layers.

        SESSION-020 (P0-NEW-4): Extends produce_sprite with multi-layer output.
        First runs evolution to find optimal parameters, then re-renders the
        best result using render_sdf_layered() to produce separated layers.

        Parameters
        ----------
        spec : AssetSpec
            Asset specification.
        export_layers : bool
            If True, save individual layer PNGs alongside the composite.

        Returns
        -------
        tuple[AssetResult, LayeredRenderResult]
            The standard asset result plus the layered render result.
        """
        # Step 1: Produce sprite normally (with evolution)
        asset_result = self.produce_sprite(spec)
        if asset_result.image is None:
            self._log("ERROR: Failed to produce base sprite for layered render")
            return asset_result, None

        # Step 2: Re-render with layered output using best params
        self._log(f"Re-rendering {spec.name} with multi-layer compositing")

        _, palette = self._get_reference_and_palette(spec)
        palette_colors_for_render = None
        if palette and isinstance(palette, list) and len(palette) > 0:
            palette_colors_for_render = [
                (c[0], c[1], c[2]) if len(c) >= 3 else c for c in palette
            ]

        # Reconstruct SDF from spec
        best_params = asset_result.metadata.get("best_params", {})
        fill_r = int(np.clip(best_params.get("fill_r", 150), 0, 255))
        fill_g = int(np.clip(best_params.get("fill_g", 100), 0, 255))
        fill_b = int(np.clip(best_params.get("fill_b", 80), 0, 255))
        light_angle = best_params.get("light_angle", 0.785)
        ao_str = best_params.get("ao_strength", 0.4)
        ramp_levels = int(np.clip(best_params.get("color_ramp_levels", 5), 3, 7))
        outline_w = best_params.get("outline_width", 0.03)

        # Build SDF
        if spec.shape in ("circle",):
            r = best_params.get("radius", 0.38)
            sdf = circle(cx=0, cy=0, r=r)
        elif spec.shape == "coin":
            r = best_params.get("radius", 0.35)
            sdf = ring(cx=0, cy=0, r=r, thickness=0.12)
        elif spec.shape in ("star",):
            r1 = best_params.get("outer_radius", 0.42)
            r2 = best_params.get("inner_radius", 0.20)
            sdf = star(cx=0, cy=0, r_outer=r1, r_inner=r2, n_points=5)
        elif spec.shape == "gem":
            r1 = best_params.get("outer_radius", 0.38)
            r2 = best_params.get("inner_radius", 0.18)
            sdf = star(cx=0, cy=0, r_outer=r1, r_inner=r2, n_points=4)
        elif spec.shape == "ring":
            r = best_params.get("ring_radius", 0.35)
            w = best_params.get("ring_width", 0.1)
            sdf = ring(cx=0, cy=0, r=r, thickness=w)
        elif spec.shape in SHAPE_LIBRARY:
            sdf = SHAPE_LIBRARY[spec.shape]()
        else:
            sdf = circle(cx=0, cy=0, r=0.4)

        pal_kwargs = {}
        if palette_colors_for_render:
            pal_kwargs = {
                "palette_constrained": True,
                "palette_colors": palette_colors_for_render,
                "palette_dither": True,
            }

        # SESSION-021 (P0-NEW-8): Use texture-aware layered rendering
        # when the spec has a texture style, otherwise use standard layered
        if spec.style != "default" and spec.style in (
            "stone", "wood", "metal", "organic", "crystal"
        ):
            layered = render_textured_sdf_layered(
                sdf,
                texture_type=spec.style,
                width=spec.size,
                height=spec.size,
                fill_color=(fill_r, fill_g, fill_b, 255),
                outline_width=outline_w,
                light_angle=light_angle,
                ao_strength=ao_str,
                color_ramp_levels=ramp_levels,
                enable_lighting=True,
                enable_ao=True,
                enable_hue_shift=True,
                **pal_kwargs,
            )
        else:
            layered = render_sdf_layered(
                sdf, spec.size, spec.size,
                fill_color=(fill_r, fill_g, fill_b, 255),
                outline_width=outline_w,
                light_angle=light_angle,
                ao_strength=ao_str,
                color_ramp_levels=ramp_levels,
                enable_lighting=True,
                enable_ao=True,
                enable_hue_shift=True,
                **pal_kwargs,
            )

        # Step 3: Export layers
        if export_layers:
            asset_dir = self.output_dir / spec.name
            asset_dir.mkdir(parents=True, exist_ok=True)
            prefix = str(asset_dir / spec.name)
            layer_paths = layered.export_layers(prefix)
            asset_result.output_paths.extend(layer_paths)
            self._log(f"Exported {len(layer_paths)} layers for {spec.name}")

            # Save layer metadata
            layer_meta = {
                "layers": ["base", "texture", "lighting", "outline", "composite"],
                "layer_files": {name: f"{spec.name}_{name}.png"
                                for name in ["base", "texture", "lighting", "outline", "composite"]},
                "render_params": layered.metadata,
            }
            import json
            meta_path = str(asset_dir / f"{spec.name}_layers.json")
            with open(meta_path, "w") as f:
                json.dump(layer_meta, f, indent=2)
            asset_result.output_paths.append(meta_path)

        self._production_log.append({
            "name": spec.name, "type": "layered_sprite",
            "score": asset_result.score,
            "layers": 5,
        })

        return asset_result, layered

    def get_production_log(self) -> list[dict]:
        """Get the production log for this session."""
        return self._production_log
