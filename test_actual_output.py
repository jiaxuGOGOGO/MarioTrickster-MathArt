"""Test actual output quality of the project - what can it really produce?"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from PIL import Image
import numpy as np

out_dir = Path("test_outputs")
out_dir.mkdir(exist_ok=True)

# 1. Test SDF rendering
print("=== Test 1: SDF Rendering ===")
from mathart.sdf.primitives import circle, box, star, triangle, ring
from mathart.sdf.renderer import render_sdf, render_spritesheet
from mathart.sdf.operations import union, intersection, smooth_union, subtraction
from mathart.sdf.effects import saw_blade_sdf, glow_sdf, flame_sdf, spike_sdf
from mathart.oklab.palette import PaletteGenerator

gen = PaletteGenerator(seed=42)
palette = gen.generate("warm_cool_shadow", count=6)

for name, sdf_fn in [("circle", circle()), ("box", box()), ("star", star()), ("triangle", triangle()), ("ring", ring())]:
    img = render_sdf(sdf_fn, 64, 64, palette)
    img.save(out_dir / f"sdf_{name}.png")
    arr = np.array(img)
    unique = len(np.unique(arr.reshape(-1, 4), axis=0))
    print(f"  {name}: {img.size}, unique colors: {unique}")

# Game elements
for name, sdf_fn in [("flame", flame_sdf()), ("spike", spike_sdf()), ("glow", glow_sdf())]:
    img = render_sdf(sdf_fn, 64, 64, palette)
    img.save(out_dir / f"sdf_{name}.png")
    print(f"  {name}: {img.size}")

# Animated saw blade
def saw_animated(x, y, t):
    return saw_blade_sdf(t=t)(x, y)
sheet = render_spritesheet(saw_animated, frames=8, width=64, height=64, palette=palette)
sheet.save(out_dir / f"spritesheet_saw.png")
print(f"  saw spritesheet: {sheet.size}")

# SDF combinations
combined = smooth_union(circle(-0.3, 0, 0.3), circle(0.3, 0, 0.3), k=0.2)
img = render_sdf(combined, 64, 64, palette)
img.save(out_dir / "sdf_smooth_union.png")
print(f"  smooth_union: {img.size}")

# 2. Test Noise Textures
print("\n=== Test 2: Noise Textures ===")
from mathart.sdf.noise import render_noise_texture, perlin_2d, fbm

for preset_name, colormap in [("terrain", "earth"), ("lava", "lava"), ("water", "water"), ("stone", "stone"), ("clouds", "sky")]:
    noise = fbm(64, 64, scale=4.0, octaves=6)
    tex = render_noise_texture(noise, colormap=colormap)
    tex.save(out_dir / f"noise_{preset_name}.png")
    print(f"  {preset_name}: {tex.size}, mode={tex.mode}")

# 3. Test Character Rendering
print("\n=== Test 3: Character Rendering ===")
try:
    from mathart.animation.skeleton import Skeleton
    from mathart.animation.character_renderer import (
        render_character_frame, render_character_sheet, CharacterStyle
    )
    from mathart.animation.presets import idle_animation, run_animation, jump_animation
    from mathart.animation.character_presets import mario_style, mario_palette, get_preset

    style, char_palette = get_preset("mario")
    skel = Skeleton.create_humanoid(3.0)
    
    # Static pose
    frame = render_character_frame(skel, {}, style, 64, 64, char_palette)
    frame.save(out_dir / "character_idle.png")
    arr = np.array(frame)
    unique = len(np.unique(arr.reshape(-1, 4), axis=0))
    print(f"  idle frame: {frame.size}, unique colors: {unique}")
    
    # Walk animation
    walk_sheet = render_character_sheet(skel, run_animation, style, frames=8, frame_width=64, frame_height=64, palette=char_palette)
    walk_sheet.save(out_dir / "character_walk.png")
    print(f"  walk sheet: {walk_sheet.size}")
    
    # Jump animation
    jump_sheet = render_character_sheet(skel, jump_animation, style, frames=8, frame_width=64, frame_height=64, palette=char_palette)
    jump_sheet.save(out_dir / "character_jump.png")
    print(f"  jump sheet: {jump_sheet.size}")
    
    # All presets
    for preset_name in ["mario", "trickster", "simple_enemy", "flying_enemy", "bouncing_enemy"]:
        try:
            s, p = get_preset(preset_name)
            f = render_character_frame(Skeleton.create_humanoid(3.0), {}, s, 64, 64, p)
            f.save(out_dir / f"character_{preset_name}.png")
            print(f"  preset {preset_name}: OK")
        except Exception as e:
            print(f"  preset {preset_name}: FAILED - {e}")
    
except Exception as e:
    print(f"  Character rendering failed: {e}")
    import traceback; traceback.print_exc()

# 4. Test L-System Plants
print("\n=== Test 4: L-System Plants ===")
try:
    from mathart.sdf.lsystem import LSystem, PlantPresets
    
    for name, lsys in PlantPresets.all_presets().items():
        segments = lsys.generate(iterations=4)
        img = lsys.render(segments, width=128, height=128)
        img.save(out_dir / f"lsystem_{name}.png")
        print(f"  {name}: {img.size}, segments: {len(segments)}")
except Exception as e:
    print(f"  L-System failed: {e}")
    import traceback; traceback.print_exc()

# 5. Test Palette Generation
print("\n=== Test 5: Palette Generation ===")
for strategy in ["analogous", "complementary", "triadic", "split_complementary", "warm_cool_shadow", "tonal_ramp"]:
    try:
        p = gen.generate(strategy, count=8)
        srgb = p.colors_srgb
        print(f"  {strategy}: {len(srgb)} colors")
    except Exception as e:
        print(f"  {strategy} failed: {e}")

# 6. Test Evolution (very short run)
print("\n=== Test 6: Evolution (5 iterations) ===")
try:
    from mathart.evolution.engine import SelfEvolutionEngine
    from mathart.evolution.inner_loop import RunMode
    from mathart.distill.compiler import ParameterSpace, Constraint

    space = ParameterSpace(
        constraints={
            "radius": Constraint(name="radius", min_value=0.2, max_value=0.8),
            "cx": Constraint(name="cx", min_value=-0.3, max_value=0.3),
            "cy": Constraint(name="cy", min_value=-0.3, max_value=0.3),
        }
    )
    
    def generator(params, progress_callback=None):
        sdf_fn = circle(params.get("cx", 0), params.get("cy", 0), params.get("radius", 0.5))
        img = render_sdf(sdf_fn, 32, 32, palette)
        if progress_callback:
            progress_callback(img, 1, 1)
        return img
    
    engine = SelfEvolutionEngine(
        project_root=Path("."), mode=RunMode.AUTONOMOUS, verbose=True
    )
    result = engine.run(
        generator=generator,
        space=space,
        palette=palette,
        max_iterations=5,
        population_size=10,
        seed=42,
    )
    print(f"  Best score: {result.best_score:.4f}")
    print(f"  Converged: {result.converged}")
    print(f"  Iterations: {result.iterations}")
    if result.best_image:
        result.best_image.save(out_dir / "evolved_result.png")
        print(f"  Saved evolved result")
except Exception as e:
    print(f"  Evolution failed: {e}")
    import traceback; traceback.print_exc()

# 7. Test Evaluator
print("\n=== Test 7: Evaluator ===")
try:
    from mathart.evaluator.evaluator import AssetEvaluator
    evaluator = AssetEvaluator()
    
    test_img = Image.open(out_dir / "character_idle.png") if (out_dir / "character_idle.png").exists() else render_sdf(circle(), 64, 64, palette)
    result = evaluator.evaluate(test_img, palette=palette)
    print(f"  Overall score: {result.overall_score:.4f}")
    for metric, mr in result.breakdown.items():
        print(f"    {metric.value}: {mr.score:.4f} ({'PASS' if mr.passed else 'FAIL'})")
except Exception as e:
    print(f"  Evaluator failed: {e}")
    import traceback; traceback.print_exc()

print("\n=== All outputs saved to test_outputs/ ===")
print(f"Total files: {len(list(out_dir.glob('*.png')))}")
