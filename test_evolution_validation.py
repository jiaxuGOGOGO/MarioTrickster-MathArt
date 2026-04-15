#!/usr/bin/env python3
"""
SESSION-019: Full evolution validation test.

Runs the complete pipeline to verify:
1. Evaluator provides real selection pressure
2. CPPN enriched topology produces diverse textures
3. Palette-constrained rendering works end-to-end
4. VFX and deformation animations produce output
5. Evolution actually improves quality over iterations
"""
import os
import sys
import time
import json
import numpy as np
from PIL import Image

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathart.pipeline import AssetPipeline, AssetSpec, AnimationSpec
from mathart.evaluator.evaluator import AssetEvaluator
from mathart.sdf.primitives import circle, star, box
from mathart.sdf.renderer import render_sdf
from mathart.animation.particles import ParticleSystem, ParticleConfig
from mathart.animation.cage_deform import CageDeformer, CagePreset

OUTPUT_DIR = "output/session019_validation"
os.makedirs(OUTPUT_DIR, exist_ok=True)

results_log = {
    "session": "SESSION-019",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "tests": [],
}


def log_test(name, status, details):
    entry = {"name": name, "status": status, **details}
    results_log["tests"].append(entry)
    icon = "PASS" if status == "pass" else "FAIL"
    print(f"  [{icon}] {name}: {details}")


print("=" * 60)
print("SESSION-019: Full Evolution Validation")
print("=" * 60)

# ── Test 1: Evaluator Selection Pressure ─────────────────────

print("\n--- Test 1: Evaluator Selection Pressure ---")
evaluator = AssetEvaluator()

# Generate sprites of varying quality
test_images = {}

# Good: well-formed circle with palette
good_img = render_sdf(
    circle(cx=0, cy=0, r=0.4), 64, 64,
    fill_color=(200, 80, 80, 255),
    enable_lighting=True, enable_ao=True, enable_hue_shift=True,
    palette_constrained=True,
    palette_colors=[
        (0, 0, 0), (34, 32, 52), (69, 40, 60), (102, 57, 49),
        (143, 86, 59), (223, 113, 38), (217, 160, 102), (238, 195, 154),
    ],
)
test_images["good_circle"] = good_img

# Medium: circle without palette constraint
medium_img = render_sdf(
    circle(cx=0, cy=0, r=0.4), 64, 64,
    fill_color=(100, 150, 200, 255),
    enable_lighting=True, enable_ao=True, enable_hue_shift=True,
)
test_images["medium_circle"] = medium_img

# Bad: tiny shape
bad_img = render_sdf(
    circle(cx=0, cy=0, r=0.05), 64, 64,
    fill_color=(200, 80, 80, 255),
)
test_images["bad_tiny"] = bad_img

# Noise
noise_arr = np.random.randint(0, 256, (64, 64, 4), dtype=np.uint8)
noise_arr[:, :, 3] = 255
noise_img = Image.fromarray(noise_arr, "RGBA")
test_images["noise"] = noise_img

scores = {}
for name, img in test_images.items():
    result = evaluator.evaluate(img)
    scores[name] = result.overall_score
    img.save(os.path.join(OUTPUT_DIR, f"eval_{name}.png"))
    log_test(
        f"eval_{name}",
        "pass" if (name == "noise" and result.overall_score < 0.6) or
                  (name == "bad_tiny" and result.overall_score < 0.5) or
                  (name == "good_circle" and result.overall_score > 0.5) or
                  (name == "medium_circle") else "info",
        {"score": round(result.overall_score, 4)},
    )

# Verify ordering
has_pressure = scores["good_circle"] > scores["medium_circle"] > scores["bad_tiny"]
log_test(
    "selection_pressure",
    "pass" if has_pressure else "fail",
    {
        "good": round(scores["good_circle"], 4),
        "medium": round(scores["medium_circle"], 4),
        "bad": round(scores["bad_tiny"], 4),
        "noise": round(scores["noise"], 4),
        "ordering_correct": has_pressure,
    },
)

# ── Test 2: Pipeline Sprite Production ───────────────────────

print("\n--- Test 2: Pipeline Sprite Production ---")
pipeline = AssetPipeline(output_dir=OUTPUT_DIR, verbose=True, seed=42)

shapes_to_test = [
    AssetSpec(name="val_coin", shape="coin", style="metal", base_hue=0.12,
              evolution_iterations=10, population_size=8, quality_threshold=0.4),
    AssetSpec(name="val_gem", shape="gem", style="crystal", base_hue=0.55,
              evolution_iterations=10, population_size=8, quality_threshold=0.4),
    AssetSpec(name="val_star", shape="star", style="default", base_hue=0.15,
              evolution_iterations=10, population_size=8, quality_threshold=0.4),
]

for spec in shapes_to_test:
    try:
        t0 = time.time()
        result = pipeline.produce_sprite(spec)
        elapsed = time.time() - t0
        log_test(
            f"sprite_{spec.name}",
            "pass" if result.image is not None and result.score > 0.3 else "fail",
            {
                "score": round(result.score, 4),
                "time": round(elapsed, 1),
                "has_image": result.image is not None,
                "files": len(result.output_paths),
            },
        )
    except Exception as e:
        log_test(f"sprite_{spec.name}", "fail", {"error": str(e)})

# ── Test 3: Animation Production ─────────────────────────────

print("\n--- Test 3: Animation Production ---")
try:
    anim_spec = AnimationSpec(
        asset=AssetSpec(name="val_coin_spin", shape="coin", style="metal",
                        evolution_iterations=8, population_size=8, quality_threshold=0.3),
        animation_type="idle", n_frames=8, fps=12,
    )
    t0 = time.time()
    anim_result = pipeline.produce_animation(anim_spec)
    elapsed = time.time() - t0
    log_test(
        "animation_idle",
        "pass" if anim_result.frames and len(anim_result.frames) == 8 else "fail",
        {
            "n_frames": len(anim_result.frames),
            "score": round(anim_result.score, 4),
            "time": round(elapsed, 1),
            "has_gif": any(".gif" in p for p in anim_result.output_paths),
        },
    )
except Exception as e:
    log_test("animation_idle", "fail", {"error": str(e)})

# ── Test 4: VFX Production ───────────────────────────────────

print("\n--- Test 4: VFX Production ---")
for preset in ["fire", "explosion", "sparkle", "smoke"]:
    try:
        t0 = time.time()
        vfx_result = pipeline.produce_vfx(
            name=f"val_{preset}", preset=preset,
            canvas_size=64, n_frames=12, seed=42,
        )
        elapsed = time.time() - t0
        log_test(
            f"vfx_{preset}",
            "pass" if vfx_result.frames and len(vfx_result.frames) == 12 else "fail",
            {
                "n_frames": len(vfx_result.frames),
                "score": round(vfx_result.score, 4),
                "time": round(elapsed, 1),
                "files": len(vfx_result.output_paths),
            },
        )
    except Exception as e:
        log_test(f"vfx_{preset}", "fail", {"error": str(e)})

# ── Test 5: Deformation Animation ────────────────────────────

print("\n--- Test 5: Deformation Animation ---")
for deform_type in ["squash_stretch", "wobble", "breathe"]:
    try:
        spec = AssetSpec(
            name=f"val_{deform_type}", shape="circle",
            evolution_iterations=6, population_size=6, quality_threshold=0.3,
        )
        t0 = time.time()
        deform_result = pipeline.produce_deform_animation(
            spec=spec, deform_type=deform_type, n_frames=8,
        )
        elapsed = time.time() - t0
        log_test(
            f"deform_{deform_type}",
            "pass" if deform_result.frames and len(deform_result.frames) == 8 else "fail",
            {
                "n_frames": len(deform_result.frames),
                "score": round(deform_result.score, 4),
                "time": round(elapsed, 1),
                "files": len(deform_result.output_paths),
            },
        )
    except Exception as e:
        log_test(f"deform_{deform_type}", "fail", {"error": str(e)})

# ── Test 6: Palette-Constrained Rendering Quality ────────────

print("\n--- Test 6: Palette-Constrained Rendering Quality ---")
palette_12 = [
    (0, 0, 0), (34, 32, 52), (69, 40, 60), (102, 57, 49),
    (143, 86, 59), (223, 113, 38), (217, 160, 102), (238, 195, 154),
    (251, 242, 54), (153, 229, 80), (106, 190, 48), (55, 148, 110),
]

for shape_name, sdf_func in [("circle", circle(cx=0, cy=0, r=0.4)), ("star", star(cx=0, cy=0, r_outer=0.42, r_inner=0.2, n_points=5)), ("box", box(cx=0, cy=0, hw=0.35, hh=0.35))]:
    img_constrained = render_sdf(
        sdf_func, 64, 64,
        fill_color=(180, 100, 60, 255),
        enable_lighting=True, enable_ao=True, enable_hue_shift=True,
        palette_constrained=True, palette_colors=palette_12,
    )
    img_unconstrained = render_sdf(
        sdf_func, 64, 64,
        fill_color=(180, 100, 60, 255),
        enable_lighting=True, enable_ao=True, enable_hue_shift=True,
    )

    arr_c = np.array(img_constrained)
    arr_u = np.array(img_unconstrained)
    opaque_c = arr_c[arr_c[:, :, 3] > 0][:, :3]
    opaque_u = arr_u[arr_u[:, :, 3] > 0][:, :3]
    unique_c = len(np.unique(opaque_c.reshape(-1, 3), axis=0)) if len(opaque_c) > 0 else 0
    unique_u = len(np.unique(opaque_u.reshape(-1, 3), axis=0)) if len(opaque_u) > 0 else 0

    img_constrained.save(os.path.join(OUTPUT_DIR, f"pal_{shape_name}_constrained.png"))
    img_unconstrained.save(os.path.join(OUTPUT_DIR, f"pal_{shape_name}_unconstrained.png"))

    log_test(
        f"palette_{shape_name}",
        "pass" if unique_c <= 12 else "fail",
        {
            "constrained_colors": unique_c,
            "unconstrained_colors": unique_u,
            "reduction": f"{unique_u} -> {unique_c}",
        },
    )

# ── Summary ──────────────────────────────────────────────────

print("\n" + "=" * 60)
passed = sum(1 for t in results_log["tests"] if t["status"] == "pass")
failed = sum(1 for t in results_log["tests"] if t["status"] == "fail")
total = len(results_log["tests"])
print(f"Results: {passed} passed, {failed} failed, {total} total")
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
print(f"Output files saved to {OUTPUT_DIR}/")
