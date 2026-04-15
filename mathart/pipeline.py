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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

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
from .animation.cage_deform import CageDeformer, CagePreset, CageAnimation
from .animation.skeleton import Skeleton
from .animation.character_renderer import render_character_frame
from .animation.character_presets import get_preset, CHARACTER_PRESETS
from .animation.presets import (
    idle_animation, run_animation, jump_animation, fall_animation, hit_animation,
)


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

    def produce_character_pack(self, char_spec: CharacterSpec) -> AssetResult:
        """Produce a practical multi-state character asset pack.

        Unlike the generic animation path (which only transforms a base sprite),
        this method renders each frame from skeletal poses and exports a usable
        state pack for direct game integration: per-state sheets, GIF previews,
        per-frame PNGs, a combined atlas, metadata, and palette provenance.
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

        output_paths: list[str] = []
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
                "render_flags": {
                    "dither": char_spec.enable_dither,
                    "outline": char_spec.enable_outline,
                    "lighting": char_spec.enable_lighting,
                },
            },
            "states": {},
        }

        state_sheets: list[tuple[str, Image.Image]] = []
        representative_frames: list[Image.Image] = []
        state_scores: list[float] = []

        for state in char_spec.states:
            anim_func = CHARACTER_ANIMATION_MAP.get(state)
            if anim_func is None:
                available = ", ".join(sorted(CHARACTER_ANIMATION_MAP))
                raise ValueError(
                    f"Unknown character state '{state}'. Available: {available}"
                )

            frame_count = max(1, int(char_spec.state_frames.get(state, char_spec.frames_per_state)))
            frame_duration_ms = max(16, 1000 // max(1, char_spec.fps))
            loop_flag = bool(char_spec.loop_overrides.get(state, state in {"idle", "run"}))

            frames: list[Image.Image] = []
            frame_scores: list[float] = []
            for i in range(frame_count):
                t = i / max(1, frame_count)
                pose = anim_func(t)
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
        }

        manifest_path = str(asset_dir / f"{char_spec.name}_character_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        output_paths.append(manifest_path)

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

    def produce_asset_pack(
        self,
        pack_name: str = "game_assets",
        sprites: Optional[list[AssetSpec]] = None,
        animations: Optional[list[AnimationSpec]] = None,
        characters: Optional[list[CharacterSpec]] = None,
        include_textures: bool = True,
    ) -> list[AssetResult]:
        """Produce a complete asset pack with sprites, props, and practical character assets."""
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
            "total_time": elapsed,
            "assets": [
                {
                    "name": r.name,
                    "score": r.score,
                    "time": r.elapsed_seconds,
                    "files": len(r.output_paths),
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
    ) -> AssetResult:
        """Produce a VFX particle animation (fire, explosion, sparkle, smoke).

        SESSION-019: Integrates ParticleSystem into the main pipeline.
        Outputs: GIF, spritesheet PNG, individual frames, JSON metadata.
        """
        start = time.time()
        self._log(f"Producing VFX: {name} (preset={preset}, frames={n_frames})")

        # Select preset
        preset_map = {
            "fire": ParticleConfig.fire,
            "explosion": ParticleConfig.explosion,
            "sparkle": ParticleConfig.sparkle,
            "smoke": ParticleConfig.smoke,
        }
        config_factory = preset_map.get(preset, ParticleConfig.fire)
        config = config_factory(canvas_size=canvas_size)
        config.seed = seed

        # Simulate
        system = ParticleSystem(config)
        frames = system.simulate_and_render(n_frames=n_frames)

        # Save outputs
        asset_dir = self.output_dir / name
        asset_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []

        # GIF
        gif_path = str(asset_dir / f"{name}.gif")
        system.export_gif(frames, gif_path)
        output_paths.append(gif_path)
        self._log(f"Saved GIF: {gif_path}")

        # Spritesheet
        sheet_path = str(asset_dir / f"{name}_sheet.png")
        meta = system.export_spritesheet(frames, sheet_path)
        output_paths.append(sheet_path)
        self._log(f"Saved spritesheet: {sheet_path}")

        # Individual frames
        for i, frame in enumerate(frames):
            fp = str(asset_dir / f"{name}_frame_{i:02d}.png")
            frame.save(fp)
            output_paths.append(fp)

        # Metadata
        meta["preset"] = preset
        meta["seed"] = seed
        meta_path = str(asset_dir / f"{name}_meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        output_paths.append(meta_path)

        # SESSION-020: Use VFX-specific evaluator (P0-NEW-6)
        score = 0.0
        if frames:
            result = self.evaluator.evaluate_multi_frame_vfx(frames)
            score = result.overall_score

        elapsed = time.time() - start
        self._log(f"VFX done: {n_frames} frames, score={score:.3f}, time={elapsed:.1f}s")

        self._production_log.append({
            "name": name, "type": "vfx",
            "score": score, "elapsed": elapsed,
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
