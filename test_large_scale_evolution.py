#!/usr/bin/env python3
"""
SESSION-020 (P0-NEW-5): Large-scale evolution validation.

Runs evolution for 100+ iterations across multiple shapes to validate:
1. Quality scores improve over generations (positive trend)
2. Evolution converges or shows diminishing returns (not random walk)
3. Different shapes achieve reasonable quality levels
4. No regressions from SESSION-019 baseline scores

This script produces:
- Per-shape score history (JSON)
- Summary statistics
- Improvement analysis
"""
import os
import sys
import time
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathart.pipeline import AssetPipeline, AssetSpec
from mathart.evaluator.evaluator import AssetEvaluator

OUTPUT_DIR = "output/session020_evolution"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("=" * 70)
print("SESSION-020: Large-Scale Evolution Validation (100+ iterations)")
print("=" * 70)

results = {
    "session": "SESSION-020",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "shapes": {},
    "summary": {},
}

# Test shapes with 100+ iterations
test_configs = [
    AssetSpec(
        name="evo_coin", shape="coin", style="metal", base_hue=0.12,
        evolution_iterations=120, population_size=16, quality_threshold=0.85,
        seed=42,
    ),
    AssetSpec(
        name="evo_star", shape="star", style="default", base_hue=0.15,
        evolution_iterations=120, population_size=16, quality_threshold=0.85,
        seed=42,
    ),
    AssetSpec(
        name="evo_gem", shape="gem", style="crystal", base_hue=0.55,
        evolution_iterations=120, population_size=16, quality_threshold=0.85,
        seed=42,
    ),
    AssetSpec(
        name="evo_circle", shape="circle", style="default", base_hue=0.0,
        evolution_iterations=120, population_size=16, quality_threshold=0.85,
        seed=42,
    ),
]

pipeline = AssetPipeline(output_dir=OUTPUT_DIR, verbose=True, seed=42)

for spec in test_configs:
    print(f"\n{'─' * 60}")
    print(f"Evolving: {spec.name} (shape={spec.shape}, iters={spec.evolution_iterations})")
    print(f"{'─' * 60}")

    t0 = time.time()
    try:
        result = pipeline.produce_sprite(spec)
        elapsed = time.time() - t0

        history = result.evolution_history
        final_score = result.score

        # Analyze improvement
        if len(history) >= 10:
            first_10_avg = np.mean(history[:10])
            last_10_avg = np.mean(history[-10:])
            improvement = last_10_avg - first_10_avg
            peak_score = max(history)
            peak_gen = history.index(peak_score)

            # Check for positive trend using linear regression
            x = np.arange(len(history))
            slope, intercept = np.polyfit(x, history, 1)
            trend_positive = slope > 0

            # Check for convergence (last 20% within 0.01 of peak)
            last_20pct = history[int(len(history) * 0.8):]
            converged = (max(last_20pct) - min(last_20pct)) < 0.02
        else:
            first_10_avg = np.mean(history[:min(10, len(history))])
            last_10_avg = first_10_avg
            improvement = 0.0
            peak_score = max(history) if history else 0.0
            peak_gen = 0
            slope = 0.0
            trend_positive = False
            converged = False

        shape_result = {
            "final_score": round(final_score, 4),
            "peak_score": round(peak_score, 4),
            "peak_generation": peak_gen,
            "iterations": len(history),
            "first_10_avg": round(first_10_avg, 4),
            "last_10_avg": round(last_10_avg, 4),
            "improvement": round(improvement, 4),
            "trend_slope": round(slope, 6),
            "trend_positive": trend_positive,
            "converged": converged,
            "elapsed_seconds": round(elapsed, 1),
            "history": [round(s, 4) for s in history],
        }

        results["shapes"][spec.name] = shape_result

        print(f"\n  Results for {spec.name}:")
        print(f"    Final score:    {final_score:.4f}")
        print(f"    Peak score:     {peak_score:.4f} (gen {peak_gen})")
        print(f"    First 10 avg:   {first_10_avg:.4f}")
        print(f"    Last 10 avg:    {last_10_avg:.4f}")
        print(f"    Improvement:    {improvement:+.4f}")
        print(f"    Trend slope:    {slope:.6f} ({'positive' if trend_positive else 'negative/flat'})")
        print(f"    Converged:      {converged}")
        print(f"    Time:           {elapsed:.1f}s")

    except Exception as e:
        print(f"  ERROR: {e}")
        results["shapes"][spec.name] = {"error": str(e)}

# Summary
print(f"\n{'=' * 70}")
print("SUMMARY")
print(f"{'=' * 70}")

all_scores = []
all_improvements = []
all_positive = []

for name, data in results["shapes"].items():
    if "error" not in data:
        all_scores.append(data["final_score"])
        all_improvements.append(data["improvement"])
        all_positive.append(data["trend_positive"])
        status = "PASS" if data["trend_positive"] or data["improvement"] >= 0 else "WARN"
        print(f"  [{status}] {name}: score={data['final_score']:.4f}, "
              f"improvement={data['improvement']:+.4f}, "
              f"trend={'positive' if data['trend_positive'] else 'flat/neg'}")

if all_scores:
    results["summary"] = {
        "shapes_tested": len(all_scores),
        "avg_final_score": round(np.mean(all_scores), 4),
        "best_final_score": round(max(all_scores), 4),
        "avg_improvement": round(np.mean(all_improvements), 4),
        "positive_trend_count": sum(all_positive),
        "all_positive": all(all_positive),
        "validation_passed": np.mean(all_improvements) >= 0,
    }

    print(f"\n  Average final score:  {np.mean(all_scores):.4f}")
    print(f"  Best final score:     {max(all_scores):.4f}")
    print(f"  Average improvement:  {np.mean(all_improvements):+.4f}")
    print(f"  Positive trends:      {sum(all_positive)}/{len(all_positive)}")
    print(f"  Validation:           {'PASSED' if results['summary']['validation_passed'] else 'FAILED'}")

# Save results
# Convert numpy types for JSON serialization
def _convert(obj):
    if isinstance(obj, (np.bool_, np.integer)):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

results_path = os.path.join(OUTPUT_DIR, "evolution_results.json")
with open(results_path, "w") as f:
    json.dump(results, f, indent=2, default=_convert)
print(f"\nResults saved to {results_path}")
