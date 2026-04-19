"""Taichi XPBD benchmark backend — SESSION-075 (P1-DISTILL-1B).

This backend wraps the existing ``mathart.animation.xpbd_taichi`` cloth solver
as a first-class microkernel plugin.  Its responsibility is intentionally
narrow: normalize benchmark config, select a device lane, run warm-up + repeated
samples, and return a typed ``BENCHMARK_REPORT`` manifest.

Red lines enforced by design:
1. No trunk hardcoding — registered via ``@register_backend``.
2. No top-level ``import taichi`` — optional dependency is resolved lazily
   through ``xpbd_taichi``.
3. No async timing fraud — the benchmark path relies on explicit runtime sync
   before stopping the timer.
4. No cold-start pollution — warm-up is excluded from the reported median.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.backend_types import BackendType


@register_backend(
    BackendType.TAICHI_XPBD,
    display_name="Taichi XPBD Benchmark Backend",
    version="1.0.0",
    artifact_families=(ArtifactFamily.BENCHMARK_REPORT.value,),
    capabilities=(
        BackendCapability.GPU_ACCELERATED,
        BackendCapability.PHYSICS_SIMULATION,
    ),
    input_requirements=(),
    dependencies=(),
    session_origin="SESSION-075",
    schema_version="1.0.0",
)
class TaichiXPBDBackend:
    """Registry plugin for Taichi XPBD CPU/GPU benchmark evidence."""

    _DEFAULT_FRAMES = 8
    _DEFAULT_WARMUP_FRAMES = 3
    _DEFAULT_SAMPLES = 5
    _DEFAULT_PARTICLE_BUDGET = 256
    _DEFAULT_DT = 1.0 / 60.0

    @property
    def name(self) -> str:
        return BackendType.TAICHI_XPBD.value

    @property
    def meta(self) -> BackendMeta:
        return BackendMeta(
            name=BackendType.TAICHI_XPBD,
            display_name="Taichi XPBD Benchmark Backend",
            version="1.0.0",
            artifact_families=(ArtifactFamily.BENCHMARK_REPORT.value,),
            capabilities=(
                BackendCapability.GPU_ACCELERATED,
                BackendCapability.PHYSICS_SIMULATION,
            ),
            session_origin="SESSION-075",
            schema_version="1.0.0",
        )

    def validate_config(
        self, context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
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

        requested_device = str(
            ctx.get("benchmark_device", ctx.get("device", "gpu")),
        ).strip().lower()
        if requested_device not in {"cpu", "gpu", "auto"}:
            warnings.append(
                f"benchmark_device={requested_device!r} invalid, defaulting to 'gpu'",
            )
            requested_device = "gpu"

        ctx.update({
            "benchmark_frame_count": frames,
            "benchmark_warmup_frames": warmup_frames,
            "benchmark_sample_count": sample_count,
            "particle_budget": particle_budget,
            "taichi_sub_steps": sub_steps,
            "taichi_solver_iterations": solver_iterations,
            "benchmark_dt": dt,
            "benchmark_device": requested_device,
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

    def _build_config(self, tx, context: dict[str, Any]):
        config = tx.create_default_taichi_cloth_config(int(context["particle_budget"]))
        requested_device = context["benchmark_device"]
        prefer_gpu = requested_device != "cpu"
        return replace(
            config,
            prefer_gpu=prefer_gpu,
            sub_steps=int(context["taichi_sub_steps"]),
            solver_iterations=int(context["taichi_solver_iterations"]),
        )

    def _write_report(
        self,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> Path:
        output_dir = Path(context.get("output_dir") or ".")
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = str(context.get("name") or self.name)
        path = output_dir / f"{stem}_{self.name}_benchmark.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _degraded_manifest(
        self,
        context: dict[str, Any],
        *,
        requested_device: str,
        status: Any,
    ) -> ArtifactManifest:
        frames = int(context["benchmark_frame_count"])
        payload = {
            "solver_type": "taichi_xpbd",
            "requested_device": requested_device,
            "device": "unavailable",
            "active_arch": getattr(status, "active_arch", "unavailable"),
            "frame_count": frames,
            "wall_time_ms": 0.0,
            "particles_per_second": 0.0,
            "particle_count": 0,
            "sample_count": int(context["benchmark_sample_count"]),
            "warmup_frames": int(context["benchmark_warmup_frames"]),
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
            version="1.0.0",
            session_id="SESSION-075",
            outputs={"report_file": str(report_path)},
            metadata=payload,
            quality_metrics={
                "throughput_particles_per_second": 0.0,
                "benchmark_degraded": 1.0,
            },
            tags=["benchmark", "taichi", "xpbd", "degraded"],
        )

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        tx = self._load_taichi_api()
        ctx = dict(context)
        if "benchmark_frame_count" not in ctx:
            ctx, _ = self.validate_config(ctx)

        requested_device = ctx["benchmark_device"]
        tx.reset_taichi_runtime()
        status = tx.get_taichi_xpbd_backend_status(prefer_gpu=requested_device != "cpu")
        if not status.available or not status.initialized:
            return self._degraded_manifest(
                ctx,
                requested_device=requested_device,
                status=status,
            )

        config = self._build_config(tx, ctx)
        system = tx.TaichiXPBDClothSystem(config)
        dt = float(ctx["benchmark_dt"])
        warmup_frames = int(ctx["benchmark_warmup_frames"])
        frames = int(ctx["benchmark_frame_count"])
        sample_count = int(ctx["benchmark_sample_count"])

        for _ in range(warmup_frames):
            system.advance(dt)
        system.sync()

        samples_ms: list[float] = []
        last_result = None
        for _ in range(sample_count):
            last_result = system.run(frames=frames, dt=dt, collect_diagnostics=False)
            samples_ms.append(float(last_result.seconds) * 1000.0)

        wall_time_ms = float(median(samples_ms)) if samples_ms else 0.0
        particle_count = int(config.particle_count)
        simulated_particle_steps = int(frames) * particle_count
        particles_per_second = 0.0
        if wall_time_ms > 0.0:
            particles_per_second = simulated_particle_steps / (wall_time_ms / 1000.0)

        active_arch = getattr(last_result, "active_arch", status.active_arch)
        actual_device = self._normalized_device(active_arch)
        payload = {
            "solver_type": "taichi_xpbd",
            "requested_device": requested_device,
            "device": actual_device,
            "active_arch": active_arch,
            "frame_count": frames,
            "wall_time_ms": wall_time_ms,
            "particles_per_second": particles_per_second,
            "particle_count": particle_count,
            "constraint_count": int(config.total_constraint_count),
            "sample_count": sample_count,
            "warmup_frames": warmup_frames,
            "benchmark_dt": dt,
            "samples_ms": samples_ms,
            "taichi_available": bool(status.available),
            "taichi_initialized": bool(status.initialized),
            "degraded": False,
        }
        report_path = self._write_report(ctx, payload)
        return ArtifactManifest(
            artifact_family=ArtifactFamily.BENCHMARK_REPORT.value,
            backend_type=BackendType.TAICHI_XPBD,
            version="1.0.0",
            session_id="SESSION-075",
            outputs={"report_file": str(report_path)},
            metadata=payload,
            quality_metrics={
                "throughput_particles_per_second": float(particles_per_second),
                "median_wall_time_ms": float(wall_time_ms),
            },
            tags=["benchmark", "taichi", "xpbd", actual_device],
        )


__all__ = ["TaichiXPBDBackend"]
