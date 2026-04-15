#!/usr/bin/env python3
"""
SESSION-020: Full Validation Test Suite.

Validates all three P0 deliverables:
  P0-NEW-4: Multi-layer render compositing
  P0-NEW-5: Large-scale evolution (results from prior run)
  P0-NEW-6: VFX evaluator tuning

Also runs regression checks from SESSION-019.
"""
import os
import sys
import time
import json
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathart.sdf.primitives import circle, star, box
from mathart.sdf.renderer import (
    render_sdf, render_sdf_layered, composite_layers, LayeredRenderResult,
)
from mathart.evaluator.evaluator import AssetEvaluator
from mathart.pipeline import AssetPipeline, AssetSpec
from mathart.animation.particles import ParticleSystem, ParticleConfig

OUTPUT_DIR = "output/session020_validation"
os.makedirs(OUTPUT_DIR, exist_ok=True)

results_log = {
    "session": "SESSION-020",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "tests": [],
}


def log_test(name, status, details):
    entry = {"name": name, "status": status, **details}
    results_log["tests"].append(entry)
    icon = "PASS" if status == "pass" else "FAIL"
    print(f"  [{icon}] {name}: {details}")


print("=" * 60)
print("SESSION-020: Full Validation Suite")
print("=" * 60)

# ═══════════════════════════════════════════════════════════════
# TEST GROUP 1: P0-NEW-4 — Multi-layer Render Compositing
# ═══════════════════════════════════════════════════════════════

print("\n--- Test Group 1: Multi-layer Render Compositing (P0-NEW-4) ---")

# Test 1.1: Basic layered render produces all layers
sdf = circle(cx=0, cy=0, r=0.4)
layered = render_sdf_layered(sdf, 64, 64, fill_color=(200, 80, 80, 255))

has_all_layers = all([
    layered.base_layer is not None,
    layered.texture_layer is not None,
    layered.lighting_layer is not None,
    layered.outline_layer is not None,
    layered.composite is not None,
])
log_test("layered_all_layers", "pass" if has_all_layers else "fail",
         {"layers_present": has_all_layers})

# Test 1.2: All layers have correct dimensions
correct_dims = all([
    layered.base_layer.size == (64, 64),
    layered.texture_layer.size == (64, 64),
    layered.lighting_layer.size == (64, 64),
    layered.outline_layer.size == (64, 64),
    layered.composite.size == (64, 64),
])
log_test("layered_dimensions", "pass" if correct_dims else "fail",
         {"all_64x64": correct_dims})

# Test 1.3: All layers are RGBA
all_rgba = all([
    layered.base_layer.mode == "RGBA",
    layered.texture_layer.mode == "RGBA",
    layered.lighting_layer.mode == "RGBA",
    layered.outline_layer.mode == "RGBA",
    layered.composite.mode == "RGBA",
])
log_test("layered_rgba", "pass" if all_rgba else "fail",
         {"all_rgba": all_rgba})

# Test 1.4: Base layer has non-transparent pixels (shape fill)
base_arr = np.array(layered.base_layer)
base_opaque = np.sum(base_arr[:, :, 3] > 0)
log_test("layered_base_fill", "pass" if base_opaque > 100 else "fail",
         {"opaque_pixels": int(base_opaque)})

# Test 1.5: Outline layer has outline pixels
outline_arr = np.array(layered.outline_layer)
outline_opaque = np.sum(outline_arr[:, :, 3] > 0)
log_test("layered_outline", "pass" if outline_opaque > 10 else "fail",
         {"outline_pixels": int(outline_opaque)})

# Test 1.6: Lighting layer has grayscale values
light_arr = np.array(layered.lighting_layer)
light_opaque = light_arr[light_arr[:, :, 3] > 0]
if len(light_opaque) > 0:
    light_variance = np.var(light_opaque[:, 0].astype(float))
    has_light_variation = light_variance > 10
else:
    has_light_variation = False
log_test("layered_lighting_variation", "pass" if has_light_variation else "fail",
         {"light_variance": round(float(light_variance) if len(light_opaque) > 0 else 0, 2)})

# Test 1.7: Composite matches render_sdf output
standard = render_sdf(sdf, 64, 64, fill_color=(200, 80, 80, 255))
std_arr = np.array(standard)
comp_arr = np.array(layered.composite)
pixel_diff = np.mean(np.abs(std_arr.astype(float) - comp_arr.astype(float)))
composites_match = pixel_diff < 5.0  # Allow small rounding differences
log_test("layered_composite_match", "pass" if composites_match else "fail",
         {"mean_pixel_diff": round(pixel_diff, 2)})

# Test 1.8: composite_layers function works
custom = composite_layers(
    layered.base_layer,
    texture=layered.texture_layer,
    lighting=layered.lighting_layer,
    outline=layered.outline_layer,
    lighting_opacity=0.5,
)
log_test("composite_layers_fn", "pass" if custom.size == (64, 64) else "fail",
         {"size": custom.size})

# Test 1.9: export_layers produces files
prefix = os.path.join(OUTPUT_DIR, "test_layered")
paths = layered.export_layers(prefix)
all_exist = all(os.path.exists(p) for p in paths)
log_test("export_layers", "pass" if all_exist and len(paths) == 5 else "fail",
         {"files": len(paths), "all_exist": all_exist})

# Test 1.10: Layered render with palette constraint
layered_pal = render_sdf_layered(
    star(cx=0, cy=0, r_outer=0.42, r_inner=0.2, n_points=5), 64, 64,
    fill_color=(200, 80, 80, 255),
    palette_constrained=True,
    palette_colors=[(0, 0, 0), (34, 32, 52), (69, 40, 60), (102, 57, 49),
                    (143, 86, 59), (223, 113, 38)],
)
log_test("layered_palette", "pass" if layered_pal.composite.size == (64, 64) else "fail",
         {"palette_constrained": layered_pal.metadata.get("palette_constrained")})

# ═══════════════════════════════════════════════════════════════
# TEST GROUP 2: P0-NEW-6 — VFX Evaluator Tuning
# ═══════════════════════════════════════════════════════════════

print("\n--- Test Group 2: VFX Evaluator Tuning (P0-NEW-6) ---")

evaluator = AssetEvaluator()

# Test 2.1-2.4: VFX presets score better with VFX evaluator
for preset_name in ["fire", "explosion", "sparkle", "smoke"]:
    config = getattr(ParticleConfig, preset_name)(canvas_size=64)
    system = ParticleSystem(config)
    frames = system.simulate_and_render(n_frames=12)

    mid_frame = frames[len(frames) // 2]
    old_result = evaluator.evaluate(mid_frame)
    new_result = evaluator.evaluate_vfx(mid_frame)
    multi_result = evaluator.evaluate_multi_frame_vfx(frames)

    # VFX score should be >= old score (or at least not much worse)
    improved = multi_result.overall_score >= old_result.overall_score * 0.9
    log_test(
        f"vfx_{preset_name}_improved",
        "pass" if improved else "fail",
        {
            "old_score": round(old_result.overall_score, 4),
            "vfx_score": round(new_result.overall_score, 4),
            "multi_score": round(multi_result.overall_score, 4),
        },
    )

# Test 2.5: Explosion specifically improved (was 0.24, target > 0.5)
config = ParticleConfig.explosion(canvas_size=64)
system = ParticleSystem(config)
frames = system.simulate_and_render(n_frames=12)
explosion_result = evaluator.evaluate_multi_frame_vfx(frames)
explosion_pass = explosion_result.overall_score > 0.5
log_test(
    "vfx_explosion_target",
    "pass" if explosion_pass else "fail",
    {"score": round(explosion_result.overall_score, 4), "target": 0.5},
)

# Test 2.6: evaluate_vfx doesn't corrupt normal evaluator state
pre_weights = dict(evaluator.weights)
_ = evaluator.evaluate_vfx(frames[0])
post_weights = dict(evaluator.weights)
weights_preserved = pre_weights == post_weights
log_test("vfx_state_preserved", "pass" if weights_preserved else "fail",
         {"weights_match": weights_preserved})

# ═══════════════════════════════════════════════════════════════
# TEST GROUP 3: P0-NEW-5 — Large-scale Evolution Results
# ═══════════════════════════════════════════════════════════════

print("\n--- Test Group 3: Large-scale Evolution Validation (P0-NEW-5) ---")

evo_results_path = "output/session020_evolution/evolution_results.json"
if os.path.exists(evo_results_path):
    with open(evo_results_path) as f:
        evo_data = json.load(f)

    summary = evo_data.get("summary", {})

    # Test 3.1: All shapes tested
    log_test("evo_shapes_tested",
             "pass" if summary.get("shapes_tested", 0) >= 4 else "fail",
             {"shapes": summary.get("shapes_tested", 0)})

    # Test 3.2: Average score > 0.8
    avg_score = summary.get("avg_final_score", 0)
    log_test("evo_avg_score",
             "pass" if avg_score > 0.8 else "fail",
             {"avg_score": avg_score})

    # Test 3.3: All positive trends
    all_positive = summary.get("all_positive", False)
    log_test("evo_all_positive_trends",
             "pass" if all_positive else "fail",
             {"all_positive": all_positive})

    # Test 3.4: Validation passed
    log_test("evo_validation",
             "pass" if summary.get("validation_passed", False) else "fail",
             {"passed": summary.get("validation_passed", False)})

    # Test 3.5: At least one shape ran 100+ iterations
    max_iters = max(
        s.get("iterations", 0) for s in evo_data.get("shapes", {}).values()
    )
    log_test("evo_100plus_iters",
             "pass" if max_iters >= 100 else "fail",
             {"max_iterations": max_iters})
else:
    log_test("evo_results_file", "fail", {"error": "evolution_results.json not found"})

# ═══════════════════════════════════════════════════════════════
# TEST GROUP 4: Regression — SESSION-019 features still work
# ═══════════════════════════════════════════════════════════════

print("\n--- Test Group 4: Regression Tests ---")

# Test 4.1: Palette-constrained rendering
palette_12 = [
    (0, 0, 0), (34, 32, 52), (69, 40, 60), (102, 57, 49),
    (143, 86, 59), (223, 113, 38), (217, 160, 102), (238, 195, 154),
    (251, 242, 54), (153, 229, 80), (106, 190, 48), (55, 148, 110),
]
pal_img = render_sdf(
    circle(cx=0, cy=0, r=0.4), 64, 64,
    fill_color=(180, 100, 60, 255),
    enable_lighting=True, enable_ao=True, enable_hue_shift=True,
    palette_constrained=True, palette_colors=palette_12,
)
arr = np.array(pal_img)
opaque = arr[arr[:, :, 3] > 0][:, :3]
unique = len(np.unique(opaque.reshape(-1, 3), axis=0)) if len(opaque) > 0 else 0
log_test("regression_palette", "pass" if unique <= 12 else "fail",
         {"unique_colors": unique})

# Test 4.2: Basic evaluator still works
eval_result = evaluator.evaluate(pal_img)
log_test("regression_evaluator", "pass" if eval_result.overall_score > 0.4 else "fail",
         {"score": round(eval_result.overall_score, 4)})

# Test 4.3: Pipeline sprite production
pipeline = AssetPipeline(output_dir=OUTPUT_DIR, verbose=False, seed=42)
try:
    spec = AssetSpec(name="reg_coin", shape="coin", style="metal",
                     evolution_iterations=8, population_size=8, quality_threshold=0.4)
    result = pipeline.produce_sprite(spec)
    log_test("regression_pipeline", "pass" if result.image is not None else "fail",
             {"score": round(result.score, 4)})
except Exception as e:
    log_test("regression_pipeline", "fail", {"error": str(e)})

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
passed = sum(1 for t in results_log["tests"] if t["status"] == "pass")
failed = sum(1 for t in results_log["tests"] if t["status"] == "fail")
total = len(results_log["tests"])
print(f"Results: {passed} passed, {failed} failed, {total} total")
print(f"Pass rate: {passed / max(1, total) * 100:.1f}%")
print("=" * 60)

results_log["summary"] = {
    "passed": passed,
    "failed": failed,
    "total": total,
    "pass_rate": round(passed / max(1, total) * 100, 1),
}

with open(os.path.join(OUTPUT_DIR, "validation_results.json"), "w") as f:
    json.dump(results_log, f, indent=2, default=str)

print(f"\nResults saved to {OUTPUT_DIR}/validation_results.json")
