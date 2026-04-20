#!/usr/bin/env python3
"""SESSION-085 — Real-device GPU benchmark ignition script.

This script is the end-to-end entry point for generating benchmark evidence
on a local workstation with a real CUDA GPU (e.g., RTX 4070).

Execution modes
---------------
1. **Real CUDA hardware**: Runs both ``free_fall_cloud`` and ``sparse_cloth``
   scenarios at production scale, producing a full ``BENCHMARK_REPORT`` JSON
   with ``speedup_ratio``, ``gpu_device_name``, and sparse-cloth parity
   metrics.
2. **CI sandbox / no GPU**: Gracefully degrades to CPU-only execution with
   tiny grids, producing a valid but degraded report.  CI stays 100% green.

Usage
-----
    # On a machine with RTX 4070 (or any CUDA GPU):
    python tools/run_session085_gpu_benchmark.py

    # Force CPU-only mode (useful for CI or debugging):
    python tools/run_session085_gpu_benchmark.py --cpu-only

    # Custom particle budget for heavy stress test:
    python tools/run_session085_gpu_benchmark.py --particle-budget 65536

Research grounding
------------------
- Google Benchmark: warm-up exclusion, repeated median sampling, structured JSON.
- NASA-STD-7009B: performance claims paired with parity evidence.
- Taichi SIGGRAPH Asia 2019: GPU kernel compilation and sparse access patterns.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathart.core.taichi_xpbd_backend import TaichiXPBDBackend


def _detect_cuda_available() -> bool:
    """Probe whether a real CUDA device is accessible via Taichi.

    Uses a direct ``ti.init(arch=ti.cuda)`` probe which is the most reliable
    method.  The previous approach (reset → status query) could silently fail
    on Windows when Taichi's internal state was not fully cleared between
    reset/init cycles.
    """
    try:
        import taichi as ti
        # Reset any prior state so we get a clean probe
        try:
            ti.reset()
        except Exception:
            pass
        # Direct CUDA init — the gold-standard check
        ti.init(arch=ti.cuda, default_fp=ti.f32)
        arch_str = str(ti.lang.impl.current_cfg().arch).lower()
        is_cuda = "cuda" in arch_str
        # Reset after probe so benchmark cases can re-init cleanly
        try:
            ti.reset()
        except Exception:
            pass
        return is_cuda
    except Exception:
        # If ti.init(arch=ti.cuda) throws, CUDA is not available
        try:
            import taichi as ti
            ti.reset()
        except Exception:
            pass
        return False


def run_case(
    output_dir: Path,
    name: str,
    device: str,
    scenario: str,
    *,
    particle_budget: int = 1024,
    frames: int = 30,
    warmup_frames: int = 10,
    sample_count: int = 7,
    sub_steps: int = 4,
    solver_iterations: int = 8,
) -> dict:
    """Execute a single benchmark case and return a summary dict."""
    backend = TaichiXPBDBackend()
    ctx, warnings = backend.validate_config(
        {
            "output_dir": str(output_dir),
            "name": name,
            "benchmark_device": device,
            "benchmark_scenario": scenario,
            "benchmark_frame_count": frames,
            "benchmark_warmup_frames": warmup_frames,
            "benchmark_sample_count": sample_count,
            "particle_budget": particle_budget,
            "taichi_sub_steps": sub_steps,
            "taichi_solver_iterations": solver_iterations,
            "strict_gpu_required": device == "gpu",
            "requires_gpu": device == "gpu",
        }
    )
    manifest = backend.execute(ctx)
    report = json.loads(Path(manifest.outputs["report_file"]).read_text(encoding="utf-8"))
    return {
        "name": name,
        "device_request": device,
        "scenario": scenario,
        "particle_budget": particle_budget,
        "warnings": warnings,
        "manifest": {
            "artifact_family": manifest.artifact_family,
            "report_file": manifest.outputs["report_file"],
        },
        "report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SESSION-085 GPU Benchmark Ignition Script",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help="Force CPU-only mode (skip GPU detection)",
    )
    parser.add_argument(
        "--particle-budget",
        type=int,
        default=None,
        help="Override particle budget (default: auto-scaled by mode)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override output directory",
    )
    args = parser.parse_args()

    # Detect execution environment
    cuda_available = False if args.cpu_only else _detect_cuda_available()
    mode = "real_cuda" if cuda_available else "cpu_fallback"

    # Scale parameters based on mode
    if cuda_available:
        # Production-scale for real GPU
        budget = args.particle_budget or 16384  # 128x128 grid
        frames = 60
        warmup = 15
        samples = 9
        sub_steps = 4
        solver_iters = 8
    else:
        # CI-safe tiny scale
        budget = args.particle_budget or 64  # 8x8 grid
        frames = 4
        warmup = 2
        samples = 3
        sub_steps = 2
        solver_iters = 4

    output_dir = Path(args.output_dir) if args.output_dir else (
        REPO_ROOT / "reports" / "session085_gpu_benchmark"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'=' * 60}")
    print(f"SESSION-085 GPU Benchmark Ignition")
    print(f"Mode: {mode}")
    print(f"CUDA detected: {cuda_available}")
    print(f"Particle budget: {budget}")
    print(f"Frames: {frames} | Warmup: {warmup} | Samples: {samples}")
    print(f"Sub-steps: {sub_steps} | Solver iterations: {solver_iters}")
    print(f"Output: {output_dir}")
    print(f"{'=' * 60}")

    cases: dict[str, dict] = {}

    # --- Free-fall cloud (baseline) ---
    print("\n[1/4] Running free_fall_cloud on CPU...")
    cases["free_fall_cpu"] = run_case(
        output_dir, "session085_freefall_cpu", "cpu", "free_fall_cloud",
        particle_budget=budget, frames=frames, warmup_frames=warmup,
        sample_count=samples, sub_steps=sub_steps, solver_iterations=solver_iters,
    )

    device = "gpu" if cuda_available else "cpu"
    print(f"\n[2/4] Running free_fall_cloud on {device.upper()}...")
    cases["free_fall_gpu"] = run_case(
        output_dir, "session085_freefall_gpu", device, "free_fall_cloud",
        particle_budget=budget, frames=frames, warmup_frames=warmup,
        sample_count=samples, sub_steps=sub_steps, solver_iterations=solver_iters,
    )

    # --- Sparse cloth (constraint-heavy topology) ---
    print(f"\n[3/4] Running sparse_cloth on CPU...")
    cases["sparse_cloth_cpu"] = run_case(
        output_dir, "session085_sparse_cpu", "cpu", "sparse_cloth",
        particle_budget=budget, frames=frames, warmup_frames=warmup,
        sample_count=samples, sub_steps=sub_steps, solver_iterations=solver_iters,
    )

    print(f"\n[4/4] Running sparse_cloth on {device.upper()}...")
    cases["sparse_cloth_gpu"] = run_case(
        output_dir, "session085_sparse_gpu", device, "sparse_cloth",
        particle_budget=budget, frames=frames, warmup_frames=warmup,
        sample_count=samples, sub_steps=sub_steps, solver_iterations=solver_iters,
    )

    # --- Summary ---
    summary = {
        "session": "SESSION-105",
        "benchmark_entrypoint": "tools/run_session085_gpu_benchmark.py",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_mode": mode,
        "cuda_detected": cuda_available,
        "particle_budget": budget,
        "strict_gpu_required": cuda_available,
        "cases": cases,
    }

    summary_path = output_dir / "session105_benchmark_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"\n{'=' * 60}")
    print("BENCHMARK COMPLETE")
    print(f"Summary: {summary_path}")
    print(f"{'=' * 60}")

    # Print key metrics
    for case_name, case_data in cases.items():
        r = case_data["report"]
        print(f"\n--- {case_name} ---")
        print(f"  Scenario:         {r['benchmark_scenario']}")
        print(f"  Device:           {r['device']}")
        print(f"  GPU:              {r['gpu_device_name']}")
        print(f"  Particles:        {r['particle_count']}")
        print(f"  Constraints:      {r['constraint_count']}")
        print(f"  Wall time (ms):   {r['wall_time_ms']:.2f}")
        print(f"  Speedup ratio:    {r['speedup_ratio']:.2f}x")
        print(f"  Max drift:        {r['cpu_gpu_max_drift']:.2e}")
        print(f"  RMSE:             {r['cpu_gpu_rmse']:.2e}")
        print(f"  Parity passed:    {r['parity_passed']}")
        print(f"  Degraded:         {r['degraded']}")


if __name__ == "__main__":
    main()
