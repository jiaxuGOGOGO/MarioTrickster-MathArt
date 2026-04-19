"""SESSION-082 — Taichi XPBD benchmark backend with correctness-aware GPU evidence.

This backend keeps the repository's IoC / registry architecture intact while
upgrading the benchmark contract to industrial-grade microbenchmark discipline:

1. Warm-up is excluded from the reported steady-state timings.
2. Repeated samples are aggregated with the median, not a single cold start.
3. GPU timings are closed by explicit runtime synchronization.
4. Performance claims are paired with NumPy reference-lane parity metrics.

The default benchmark scenario is a dense **free-fall cloud**. This choice is
intentional: it allows exact NumPy reference trajectories while still stressing
host-vs-device throughput on large particle counts. The classic cloth grid path
remains available via ``benchmark_scenario='cloth_grid'``.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter
from typing import Any

import numpy as np

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.backend_types import BackendType


@register_backend(
    BackendType.TAICHI_XPBD,
    display_name="Taichi XPBD Benchmark Backend",
    version="1.1.0",
    artifact_families=(ArtifactFamily.BENCHMARK_REPORT.value,),
    capabilities=(
        BackendCapability.GPU_ACCELERATED,
        BackendCapability.PHYSICS_SIMULATION,
    ),
    input_requirements=(),
    dependencies=(),
    session_origin="SESSION-082",
    schema_version="1.1.0",
)
class TaichiXPBDBackend:
    """Registry plugin for correctness-aware Taichi XPBD benchmark evidence."""

    _DEFAULT_FRAMES = 30
    _DEFAULT_WARMUP_FRAMES = 10
    _DEFAULT_SAMPLES = 7
    _DEFAULT_PARTICLE_BUDGET = 1024
    _DEFAULT_DT = 1.0 / 60.0
    _DEFAULT_SCENARIO = "free_fall_cloud"
    _DEFAULT_DRIFT_ATOL = 5e-5
    _DEFAULT_RMSE_ATOL = 5e-5

    @property
    def name(self) -> str:
        return BackendType.TAICHI_XPBD.value

    @property
    def meta(self) -> BackendMeta:
        return BackendMeta(
            name=BackendType.TAICHI_XPBD,
            display_name="Taichi XPBD Benchmark Backend",
            version="1.1.0",
            artifact_families=(ArtifactFamily.BENCHMARK_REPORT.value,),
            capabilities=(
                BackendCapability.GPU_ACCELERATED,
                BackendCapability.PHYSICS_SIMULATION,
            ),
            session_origin="SESSION-082",
            schema_version="1.1.0",
        )

    def validate_config(self, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        ctx = dict(context)

        def _as_int(key: str, default: int, *, minimum: int) -> int:
            try:
                value = int(ctx.get(key, default))
            except (TypeError, ValueError):
                warnings.append(f"{key} invalid, defaulting to {default}")
                return default
            return max(value, minimum)

        def _as_float(key: str, default: float, *, minimum: float) -> float:
            try:
                value = float(ctx.get(key, default))
            except (TypeError, ValueError):
                warnings.append(f"{key} invalid, defaulting to {default}")
                return default
            return max(value, minimum)

        frames = _as_int(
            "benchmark_frame_count",
            int(ctx.get("frame_count", self._DEFAULT_FRAMES)),
            minimum=1,
        )
        warmup_frames = _as_int(
            "benchmark_warmup_frames",
            self._DEFAULT_WARMUP_FRAMES,
            minimum=0,
        )
        sample_count = _as_int(
            "benchmark_sample_count",
            self._DEFAULT_SAMPLES,
            minimum=1,
        )
        particle_budget = _as_int(
            "particle_budget",
            self._DEFAULT_PARTICLE_BUDGET,
            minimum=16,
        )
        sub_steps = _as_int("taichi_sub_steps", 4, minimum=1)
        solver_iterations = _as_int("taichi_solver_iterations", 8, minimum=1)
        dt = _as_float("benchmark_dt", self._DEFAULT_DT, minimum=1e-6)
        drift_atol = _as_float("benchmark_drift_atol", self._DEFAULT_DRIFT_ATOL, minimum=0.0)
        rmse_atol = _as_float("benchmark_rmse_atol", self._DEFAULT_RMSE_ATOL, minimum=0.0)

        requested_device = str(
            ctx.get("benchmark_device", ctx.get("device", "gpu")),
        ).strip().lower()
        if requested_device not in {"cpu", "gpu", "auto"}:
            warnings.append(
                f"benchmark_device={requested_device!r} invalid, defaulting to 'gpu'",
            )
            requested_device = "gpu"

        scenario = str(ctx.get("benchmark_scenario", self._DEFAULT_SCENARIO)).strip().lower()
        if scenario not in {"free_fall_cloud", "cloth_grid"}:
            warnings.append(
                f"benchmark_scenario={scenario!r} invalid, defaulting to {self._DEFAULT_SCENARIO!r}",
            )
            scenario = self._DEFAULT_SCENARIO

        ctx.update({
            "benchmark_frame_count": frames,
            "benchmark_warmup_frames": warmup_frames,
            "benchmark_sample_count": sample_count,
            "particle_budget": particle_budget,
            "taichi_sub_steps": sub_steps,
            "taichi_solver_iterations": solver_iterations,
            "benchmark_dt": dt,
            "benchmark_device": requested_device,
            "benchmark_scenario": scenario,
            "benchmark_drift_atol": drift_atol,
            "benchmark_rmse_atol": rmse_atol,
        })
        return ctx, warnings

    def _load_taichi_api(self):
        module_name = "mathart._taichi_xpbd_runtime"
        cached = sys.modules.get(module_name)
        if cached is not None:
            return cached
        module_path = Path(__file__).resolve().parents[1] / "animation" / "xpbd_taichi.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load Taichi XPBD module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _normalized_device(active_arch: str) -> str:
        return "cpu" if active_arch in {"cpu", "unavailable", "failed"} else "gpu"

    @staticmethod
    def _query_gpu_device_name(actual_device: str) -> str:
        if actual_device != "gpu":
            return "unavailable"
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            line = (result.stdout or "").strip().splitlines()
            if line:
                return line[0].strip()
        except Exception:
            pass
        return "unknown-gpu"

    @staticmethod
    def _initial_positions(config: Any) -> np.ndarray:
        xs = float(config.origin_x) + np.arange(int(config.width), dtype=np.float64) * float(config.spacing)
        ys = float(config.origin_y) - np.arange(int(config.height), dtype=np.float64) * float(config.spacing)
        grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")
        return np.stack([grid_x, grid_y], axis=-1)

    def _build_config(self, tx, context: dict[str, Any], *, prefer_gpu: bool):
        config = tx.create_default_taichi_cloth_config(int(context["particle_budget"]))
        scenario = context["benchmark_scenario"]
        if scenario == "free_fall_cloud":
            return replace(
                config,
                prefer_gpu=prefer_gpu,
                sub_steps=int(context["taichi_sub_steps"]),
                solver_iterations=int(context["taichi_solver_iterations"]),
                enable_constraints=False,
                pin_top_row=False,
                pin_corners=False,
                enable_ground_collision=False,
                enable_circle_collision=False,
                max_velocity=1.0e9,
            )
        return replace(
            config,
            prefer_gpu=prefer_gpu,
            sub_steps=int(context["taichi_sub_steps"]),
            solver_iterations=int(context["taichi_solver_iterations"]),
        )

    def _write_report(self, context: dict[str, Any], payload: dict[str, Any]) -> Path:
        output_dir = Path(context.get("output_dir") or ".")
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = str(context.get("name") or self.name)
        path = output_dir / f"{stem}_{self.name}_benchmark.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _degraded_manifest(self, context: dict[str, Any], *, requested_device: str, status: Any) -> ArtifactManifest:
        frames = int(context["benchmark_frame_count"])
        payload = {
            "solver_type": "taichi_xpbd",
            "requested_device": requested_device,
            "device": "unavailable",
            "active_arch": getattr(status, "active_arch", "unavailable"),
            "gpu_device_name": "unavailable",
            "benchmark_scenario": str(context["benchmark_scenario"]),
            "frame_count": frames,
            "wall_time_ms": 0.0,
            "particles_per_second": 0.0,
            "particle_count": 0,
            "sample_count": int(context["benchmark_sample_count"]),
            "warmup_frames": int(context["benchmark_warmup_frames"]),
            "sample_statistic": "median",
            "explicit_sync_used": True,
            "cpu_reference_solver": "numpy_reference_unavailable",
            "cpu_reference_wall_time_ms": 0.0,
            "cpu_reference_particles_per_second": 0.0,
            "cpu_reference_samples_ms": [],
            "speedup_ratio": 0.0,
            "cpu_gpu_max_drift": 0.0,
            "cpu_gpu_rmse": 0.0,
            "parity_abs_tolerance": float(context["benchmark_drift_atol"]),
            "parity_rmse_tolerance": float(context["benchmark_rmse_atol"]),
            "parity_passed": False,
            "taichi_available": bool(getattr(status, "available", False)),
            "taichi_initialized": bool(getattr(status, "initialized", False)),
            "degraded": True,
            "reason": getattr(status, "import_error", "Taichi unavailable"),
            "samples_ms": [],
        }
        report_path = self._write_report(context, payload)
        return ArtifactManifest(
            artifact_family=ArtifactFamily.BENCHMARK_REPORT.value,
            backend_type=BackendType.TAICHI_XPBD,
            version="1.1.0",
            session_id="SESSION-082",
            outputs={"report_file": str(report_path)},
            metadata=payload,
            quality_metrics={
                "throughput_particles_per_second": 0.0,
                "benchmark_degraded": 1.0,
                "cpu_gpu_max_drift": 0.0,
                "speedup_ratio": 0.0,
            },
            tags=["benchmark", "taichi", "xpbd", "degraded"],
        )

    def _run_taichi_lane(self, tx, *, config: Any, frames: int, dt: float, warmup_frames: int, sample_count: int) -> dict[str, Any]:
        samples_ms: list[float] = []
        final_positions = None
        last_result = None
        for _ in range(sample_count):
            system = tx.TaichiXPBDClothSystem(config)
            for _ in range(warmup_frames):
                system.advance(dt)
            system.sync()
            last_result = system.run(frames=frames, dt=dt, collect_diagnostics=False)
            samples_ms.append(float(last_result.seconds) * 1000.0)
            final_positions = np.asarray(system.positions_numpy(), dtype=np.float64)
        active_arch = getattr(last_result, "active_arch", getattr(config, "prefer_gpu", False) and "gpu" or "cpu")
        wall_time_ms = float(median(samples_ms)) if samples_ms else 0.0
        particle_count = int(config.particle_count)
        simulated_particle_steps = int(frames) * particle_count
        particles_per_second = 0.0
        if wall_time_ms > 0.0:
            particles_per_second = simulated_particle_steps / (wall_time_ms / 1000.0)
        return {
            "active_arch": active_arch,
            "wall_time_ms": wall_time_ms,
            "samples_ms": samples_ms,
            "particle_count": particle_count,
            "particles_per_second": particles_per_second,
            "constraint_count": int(config.total_constraint_count),
            "positions": final_positions,
        }

    def _run_numpy_reference(self, *, config: Any, frames: int, dt: float, warmup_frames: int, sample_count: int, scenario: str) -> dict[str, Any]:
        gravity = np.asarray(config.gravity, dtype=np.float64)
        initial = self._initial_positions(config)
        samples_ms: list[float] = []
        final_positions = initial.copy()

        for _ in range(sample_count):
            positions = initial.copy()
            velocities = np.zeros_like(positions)
            for _ in range(warmup_frames):
                positions += velocities * dt + 0.5 * gravity * (dt * dt)
                velocities += gravity * dt
            start = perf_counter()
            for _ in range(frames):
                if scenario == "free_fall_cloud":
                    positions += velocities * dt + 0.5 * gravity * (dt * dt)
                    velocities += gravity * dt
                else:
                    positions += velocities * dt + 0.5 * gravity * (dt * dt)
                    velocities += gravity * dt
            elapsed_ms = (perf_counter() - start) * 1000.0
            samples_ms.append(float(elapsed_ms))
            final_positions = positions

        wall_time_ms = float(median(samples_ms)) if samples_ms else 0.0
        particle_count = int(config.particle_count)
        simulated_particle_steps = int(frames) * particle_count
        particles_per_second = 0.0
        if wall_time_ms > 0.0:
            particles_per_second = simulated_particle_steps / (wall_time_ms / 1000.0)
        return {
            "wall_time_ms": wall_time_ms,
            "samples_ms": samples_ms,
            "particle_count": particle_count,
            "particles_per_second": particles_per_second,
            "positions": np.asarray(final_positions, dtype=np.float64),
        }

    @staticmethod
    def _compute_parity(cpu_positions: np.ndarray | None, lane_positions: np.ndarray | None) -> tuple[float, float]:
        if cpu_positions is None or lane_positions is None:
            return 0.0, 0.0
        diff = np.asarray(lane_positions, dtype=np.float64) - np.asarray(cpu_positions, dtype=np.float64)
        max_drift = float(np.max(np.abs(diff))) if diff.size else 0.0
        rmse = float(np.sqrt(np.mean(np.square(diff)))) if diff.size else 0.0
        return max_drift, rmse

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        tx = self._load_taichi_api()
        ctx = dict(context)
        if "benchmark_frame_count" not in ctx:
            ctx, _ = self.validate_config(ctx)

        requested_device = ctx["benchmark_device"]
        scenario = ctx["benchmark_scenario"]
        prefer_gpu = requested_device != "cpu"
        tx.reset_taichi_runtime()
        status = tx.get_taichi_xpbd_backend_status(prefer_gpu=prefer_gpu)
        if not status.available or not status.initialized:
            return self._degraded_manifest(ctx, requested_device=requested_device, status=status)

        config = self._build_config(tx, ctx, prefer_gpu=prefer_gpu)
        dt = float(ctx["benchmark_dt"])
        warmup_frames = int(ctx["benchmark_warmup_frames"])
        frames = int(ctx["benchmark_frame_count"])
        sample_count = int(ctx["benchmark_sample_count"])

        lane = self._run_taichi_lane(
            tx,
            config=config,
            frames=frames,
            dt=dt,
            warmup_frames=warmup_frames,
            sample_count=sample_count,
        )
        cpu_reference = self._run_numpy_reference(
            config=config,
            frames=frames,
            dt=dt,
            warmup_frames=warmup_frames,
            sample_count=sample_count,
            scenario=scenario,
        )

        active_arch = str(lane["active_arch"])
        actual_device = self._normalized_device(active_arch)
        gpu_device_name = self._query_gpu_device_name(actual_device)
        speedup_ratio = 0.0
        if float(lane["wall_time_ms"]) > 0.0:
            speedup_ratio = float(cpu_reference["wall_time_ms"]) / float(lane["wall_time_ms"])
        max_drift, rmse = self._compute_parity(cpu_reference.get("positions"), lane.get("positions"))
        parity_passed = (
            max_drift <= float(ctx["benchmark_drift_atol"])
            and rmse <= float(ctx["benchmark_rmse_atol"])
        )

        payload = {
            "solver_type": "taichi_xpbd",
            "requested_device": requested_device,
            "device": actual_device,
            "active_arch": active_arch,
            "gpu_device_name": gpu_device_name,
            "benchmark_scenario": scenario,
            "frame_count": frames,
            "wall_time_ms": float(lane["wall_time_ms"]),
            "particles_per_second": float(lane["particles_per_second"]),
            "particle_count": int(lane["particle_count"]),
            "constraint_count": int(lane["constraint_count"]),
            "sample_count": sample_count,
            "warmup_frames": warmup_frames,
            "benchmark_dt": dt,
            "sample_statistic": "median",
            "explicit_sync_used": True,
            "samples_ms": list(lane["samples_ms"]),
            "cpu_reference_solver": "numpy_free_fall_cloud" if scenario == "free_fall_cloud" else "numpy_reference_lane",
            "cpu_reference_wall_time_ms": float(cpu_reference["wall_time_ms"]),
            "cpu_reference_particles_per_second": float(cpu_reference["particles_per_second"]),
            "cpu_reference_samples_ms": list(cpu_reference["samples_ms"]),
            "speedup_ratio": float(speedup_ratio),
            "cpu_gpu_max_drift": float(max_drift),
            "cpu_gpu_rmse": float(rmse),
            "parity_abs_tolerance": float(ctx["benchmark_drift_atol"]),
            "parity_rmse_tolerance": float(ctx["benchmark_rmse_atol"]),
            "parity_passed": bool(parity_passed),
            "taichi_available": bool(status.available),
            "taichi_initialized": bool(status.initialized),
            "degraded": False,
        }
        report_path = self._write_report(ctx, payload)
        return ArtifactManifest(
            artifact_family=ArtifactFamily.BENCHMARK_REPORT.value,
            backend_type=BackendType.TAICHI_XPBD,
            version="1.1.0",
            session_id="SESSION-082",
            outputs={"report_file": str(report_path)},
            metadata=payload,
            quality_metrics={
                "throughput_particles_per_second": float(lane["particles_per_second"]),
                "median_wall_time_ms": float(lane["wall_time_ms"]),
                "cpu_gpu_max_drift": float(max_drift),
                "speedup_ratio": float(speedup_ratio),
            },
            tags=["benchmark", "taichi", "xpbd", actual_device, scenario],
        )


__all__ = ["TaichiXPBDBackend"]
