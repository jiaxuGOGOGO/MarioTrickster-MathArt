"""
Asset Production Pipeline — The core system that ties everything together.

This module provides a high-level pipeline that:
  1. Defines what asset to produce (sprite, animation, texture, tileset)
  2. Sets up the parameter space for evolution
  3. Runs the evolution engine to find optimal parameters
  4. Renders the final asset with the best parameters
  5. Applies animation principles if needed
  6. Exports game-ready assets (PNG, spritesheet, metadata)

This is the "production line" that makes the project produce real
art assets, not just demos.

Usage::

    from mathart.pipeline import AssetPipeline, AssetSpec

    pipeline = AssetPipeline(output_dir="output/")

    # Produce a single sprite
    result = pipeline.produce_sprite(
        shape="star",
        style="crystal",
        size=64,
    )

    # Produce an animated spritesheet
    result = pipeline.produce_animation(
        shape="circle",
        animation="jump",
        frames=8,
        size=64,
    )

    # Produce a texture atlas via CPPN evolution
    result = pipeline.produce_texture_atlas(
        n_textures=25,
        evolution_steps=200,
        tile_size=64,
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
from .sdf.renderer import render_sdf, render_textured_sdf, render_spritesheet
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


# ── Shape Library ─────────────────────────────────────────────────────────────

SHAPE_LIBRARY: dict[str, Callable[..., Any]] = {
    "circle": lambda r=0.4: circle(r),
    "box": lambda w=0.35, h=0.35: box(w, h),
    "star": lambda n=5, r1=0.4, r2=0.2: star(n, r1, r2),
    "triangle": lambda s=0.4: triangle(s),
    "ring": lambda r=0.35, w=0.1: ring(r, w),
    "spike": lambda: spike_sdf(),
    "flame": lambda: flame_sdf(),
    "saw": lambda: saw_blade_sdf(),
    "glow": lambda: glow_sdf(),
    "electric": lambda: electric_arc_sdf(),
    "coin": lambda: ring(0.35, 0.12),
    "gem": lambda: star(4, 0.35, 0.15),
    "shield": lambda: smooth_union(circle(0.3), box(0.25, 0.1), k=0.1),
    "heart": lambda: smooth_union(
        circle(0.2),
        smooth_union(circle(0.2), triangle(0.3), k=0.1),
        k=0.15,
    ),
    "platform": lambda: box(0.6, 0.12),
    "bullet": lambda: smooth_union(circle(0.15), box(0.1, 0.25), k=0.08),
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

    This is the main entry point for asset production. It orchestrates
    the SDF renderer, evolution engine, animation system, and CPPN
    texture generator into a coherent production workflow.
    """

    def __init__(
        self,
        output_dir: str = "output",
        verbose: bool = True,
        seed: int = 42,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.seed = seed
        self.evaluator = AssetEvaluator()
        self.palette_gen = PaletteGenerator(seed=seed)
        self._production_log: list[dict] = []

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

        # Shape-specific parameters
        if spec.shape in ("circle", "coin"):
            space.add_constraint(Constraint(
                param_name="radius", min_value=0.25, max_value=0.5,
                default_value=0.38,
            ))
        elif spec.shape in ("star", "gem"):
            space.add_constraint(Constraint(
                param_name="outer_radius", min_value=0.3, max_value=0.5,
                default_value=0.4,
            ))
            space.add_constraint(Constraint(
                param_name="inner_radius", min_value=0.1, max_value=0.3,
                default_value=0.18,
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

    def _build_generator(self, spec: AssetSpec) -> Callable[[dict], Image.Image]:
        """Build a generator function for evolution."""

        def generator(params: dict) -> Image.Image:
            fill_r = int(np.clip(params.get("fill_r", 150), 0, 255))
            fill_g = int(np.clip(params.get("fill_g", 100), 0, 255))
            fill_b = int(np.clip(params.get("fill_b", 80), 0, 255))
            light_angle = params.get("light_angle", 0.785)
            ao_strength = params.get("ao_strength", 0.4)
            ramp_levels = int(np.clip(params.get("color_ramp_levels", 5), 3, 7))
            outline_width = params.get("outline_width", 0.03)

            # Build SDF shape
            if spec.shape in ("circle",):
                r = params.get("radius", 0.38)
                sdf = circle(r)
            elif spec.shape == "coin":
                r = params.get("radius", 0.35)
                sdf = ring(r, 0.12)
            elif spec.shape in ("star",):
                r1 = params.get("outer_radius", 0.4)
                r2 = params.get("inner_radius", 0.18)
                sdf = star(5, r1, r2)
            elif spec.shape == "gem":
                r1 = params.get("outer_radius", 0.35)
                r2 = params.get("inner_radius", 0.15)
                sdf = star(4, r1, r2)
            elif spec.shape == "ring":
                r = params.get("ring_radius", 0.35)
                w = params.get("ring_width", 0.1)
                sdf = ring(r, w)
            elif spec.shape in SHAPE_LIBRARY:
                sdf = SHAPE_LIBRARY[spec.shape]()
            else:
                sdf = circle(0.4)

            # Render
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
                    enable_dithering=True,
                    enable_ao=True,
                    enable_hue_shift=True,
                )
            return img

        return generator

    def produce_sprite(self, spec: AssetSpec) -> AssetResult:
        """Produce a single sprite through evolution.

        Steps:
          1. Build parameter space
          2. Build generator function
          3. Run evolution to find best parameters
          4. Render final sprite
          5. Save and return result
        """
        start = time.time()
        self._log(f"Producing sprite: {spec.name} (shape={spec.shape}, style={spec.style})")

        # Build components
        space = self._build_parameter_space(spec)
        generator = self._build_generator(spec)

        # Run evolution
        runner = InnerLoopRunner(
            evaluator=self.evaluator,
            quality_threshold=spec.quality_threshold,
            max_iterations=spec.evolution_iterations,
            population_size=spec.population_size,
            patience=max(5, spec.evolution_iterations // 3),
            verbose=self.verbose,
            mode=RunMode.AUTONOMOUS,
        )

        result = runner.run(
            generator=generator,
            space=space,
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

        # Save metadata
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
        """Produce an animated spritesheet.

        Steps:
          1. Produce the base sprite via evolution
          2. Create animation from principles
          3. Apply animation transforms to each frame
          4. Assemble spritesheet
          5. Save and return
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
            # Apply transforms to base sprite
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

        # Save individual frames
        for i, frame in enumerate(frames):
            frame_path = str(asset_dir / f"{spec.name}_frame_{i:02d}.png")
            frame.save(frame_path)
            output_paths.append(frame_path)

        # Save animation metadata
        anim_meta = {
            "name": spec.name,
            "animation": anim_spec.animation_type,
            "n_frames": anim_spec.n_frames,
            "fps": anim_spec.fps,
            "loop": anim_spec.loop,
            "frame_data": [
                {
                    "position": d["position"],
                    "scale": d["scale"],
                    "rotation": d["rotation"],
                    "opacity": d["opacity"],
                }
                for d in anim_data
            ],
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

    def produce_texture_atlas(
        self,
        name: str = "textures",
        n_textures: int = 25,
        evolution_steps: int = 200,
        tile_size: int = 64,
        seed: int = 42,
    ) -> AssetResult:
        """Produce a diverse texture atlas via CPPN MAP-Elites evolution.

        This generates a library of procedural textures that can be used
        as materials for SDF sprites.
        """
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
        include_textures: bool = True,
    ) -> list[AssetResult]:
        """Produce a complete asset pack with multiple sprites, animations, and textures.

        This is the highest-level production function.
        """
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

    def get_production_log(self) -> list[dict]:
        """Get the production log for this session."""
        return self._production_log
