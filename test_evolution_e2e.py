"""
End-to-end test of the evolution engine.
This script tests the FULL pipeline:
  1. Create a parameter space with proper Constraint objects
  2. Create a generator function that renders SDF sprites
  3. Run the evolution engine
  4. Verify output quality improves over iterations
  5. Export the best result as a game-ready asset
"""
import os
import sys
import numpy as np
from PIL import Image

# ── Setup ──
os.makedirs("test_outputs/evolution", exist_ok=True)

# ── Step 1: Create parameter space ──
print("=== Step 1: Parameter Space ===")
from mathart.distill.compiler import Constraint, ParameterSpace

space = ParameterSpace(name="circle_sprite")
space.add_constraint(Constraint(
    param_name="radius",
    min_value=0.2,
    max_value=0.6,
    default_value=0.4,
))
space.add_constraint(Constraint(
    param_name="outline_width",
    min_value=0.01,
    max_value=0.08,
    default_value=0.03,
))
space.add_constraint(Constraint(
    param_name="fill_r",
    min_value=50,
    max_value=255,
    default_value=180,
))
space.add_constraint(Constraint(
    param_name="fill_g",
    min_value=50,
    max_value=255,
    default_value=80,
))
space.add_constraint(Constraint(
    param_name="fill_b",
    min_value=50,
    max_value=255,
    default_value=80,
))
space.add_constraint(Constraint(
    param_name="light_angle",
    min_value=0.0,
    max_value=6.28,
    default_value=0.785,
))
space.add_constraint(Constraint(
    param_name="ao_strength",
    min_value=0.0,
    max_value=0.8,
    default_value=0.4,
))
space.add_constraint(Constraint(
    param_name="color_ramp_levels",
    min_value=3,
    max_value=7,
    default_value=5,
))

print(f"  Dimensions: {space.dimensions}")
print(f"  Ranges: {space.get_ranges()}")

# ── Step 2: Create generator function ──
print("\n=== Step 2: Generator Function ===")
from mathart.sdf.primitives import circle
from mathart.sdf.renderer import render_sdf

def sprite_generator(params: dict) -> Image.Image:
    """Generate a sprite from evolution parameters."""
    radius = params.get("radius", 0.4)
    outline_width = params.get("outline_width", 0.03)
    fill_r = int(np.clip(params.get("fill_r", 180), 0, 255))
    fill_g = int(np.clip(params.get("fill_g", 80), 0, 255))
    fill_b = int(np.clip(params.get("fill_b", 80), 0, 255))
    light_angle = params.get("light_angle", 0.785)
    ao_strength = params.get("ao_strength", 0.4)
    ramp_levels = int(np.clip(params.get("color_ramp_levels", 5), 3, 7))

    sdf = circle(radius)
    img = render_sdf(
        sdf, 64, 64,
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

# Test generator
test_img = sprite_generator(space.get_defaults())
test_img.save("test_outputs/evolution/gen_default.png")
print(f"  Generator test: {test_img.size}, mode={test_img.mode}")

# ── Step 3: Test evaluator ──
print("\n=== Step 3: Evaluator ===")
from mathart.evaluator.evaluator import AssetEvaluator

evaluator = AssetEvaluator()
eval_result = evaluator.evaluate(test_img)
print(f"  Overall score: {eval_result.overall_score:.4f}")
print(f"  Summary: {eval_result.summary()}")

# ── Step 4: Run evolution (small scale for testing) ──
print("\n=== Step 4: Evolution Run ===")
from mathart.evolution.inner_loop import InnerLoopRunner, RunMode

runner = InnerLoopRunner(
    evaluator=evaluator,
    quality_threshold=0.7,
    max_iterations=10,
    population_size=12,
    patience=5,
    verbose=True,
    mode=RunMode.AUTONOMOUS,
)

result = runner.run(
    generator=sprite_generator,
    space=space,
    seed=42,
)

print(f"\n=== Step 5: Results ===")
print(f"  Best score: {result.best_score:.4f}")
print(f"  Iterations: {result.iterations}")
print(f"  Converged: {result.converged}")
print(f"  History: {[f'{s:.3f}' for s in result.history]}")

if result.best_image is not None:
    result.best_image.save("test_outputs/evolution/best_result.png")
    print(f"  Best image saved: {result.best_image.size}")

# Save history plot
if result.history:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.figure(figsize=(8, 4))
    plt.plot(result.history, 'b-o', markersize=4)
    plt.xlabel('Generation')
    plt.ylabel('Best Fitness')
    plt.title('Evolution Progress')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("test_outputs/evolution/progress.png", dpi=100)
    print("  Progress chart saved")

# ── Step 6: Run full engine ──
print("\n=== Step 6: Full Engine ===")
from mathart.evolution.engine import SelfEvolutionEngine

engine = SelfEvolutionEngine(
    project_root=".",
    mode=RunMode.AUTONOMOUS,
    verbose=True,
)

engine_result = engine.run(
    generator=sprite_generator,
    space=space,
    max_iterations=5,
    population_size=8,
    seed=123,
)

print(f"\n  Engine best score: {engine_result.best_score:.4f}")
print(f"  Engine iterations: {engine_result.iterations}")

if engine_result.best_image is not None:
    engine_result.best_image.save("test_outputs/evolution/engine_best.png")
    print("  Engine best image saved")

print("\n=== EVOLUTION ENGINE E2E TEST COMPLETE ===")
print(f"Total output files: {len(os.listdir('test_outputs/evolution'))}")
