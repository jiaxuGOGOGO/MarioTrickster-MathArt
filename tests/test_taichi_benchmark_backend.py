"""Tests for SESSION-082 correctness-aware Taichi benchmark backend.

The suite is intentionally split into three layers:

1. Schema / registry guards that must pass everywhere.
2. Optional-dependency isolation via fake Taichi modules, ensuring CI can
   validate the backend even when ``taichi`` is unavailable.
3. A small real-runtime smoke path that exercises the free-fall cloud benchmark
   contract without assuming a CUDA device exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.core.artifact_schema import ArtifactFamily, validate_artifact
from mathart.core.backend_registry import BackendCapability, get_registry
from mathart.core.backend_types import BackendType
from mathart.core.taichi_xpbd_backend import TaichiXPBDBackend
from mathart.distill.runtime_bus import RuntimeDistillationBus


_EVOLUTION_LOOP_PATH = Path(__file__).resolve().parent.parent / "mathart" / "evolution" / "evolution_loop.py"


@dataclass(frozen=True)
class _FakeConfig:
    width: int = 4
    height: int = 4
    spacing: float = 0.02
    origin_x: float = -0.04
    origin_y: float = 0.30
    particle_mass: float = 0.04
    gravity: tuple[float, float] = (0.0, -9.81)
    sub_steps: int = 1
    solver_iterations: int = 1
    structural_compliance: float = 1e-6
    shear_compliance: float = 1e-6
    bending_compliance: float = 1e-6
    velocity_damping: float = 0.995
    max_velocity: float = 1.0e9
    enable_constraints: bool = False
    pin_top_row: bool = False
    pin_corners: bool = False
    enable_ground_collision: bool = False
    ground_y: float = -10.0
    enable_circle_collision: bool = False
    circle_center: tuple[float, float] = (0.0, 0.0)
    circle_radius: float = 0.0
    prefer_gpu: bool = True

    @property
    def particle_count(self) -> int:
        return int(self.width * self.height)

    @property
    def total_constraint_count(self) -> int:
        if not self.enable_constraints:
            return 0
        return int((self.width - 1) * self.height + self.width * (self.height - 1))


class _FakeClothSystem:
    def __init__(self, config: _FakeConfig) -> None:
        self.config = config
        self.active_arch = "cuda" if config.prefer_gpu else "cpu"
        xs = config.origin_x + np.arange(config.width, dtype=np.float64) * config.spacing
        ys = config.origin_y - np.arange(config.height, dtype=np.float64) * config.spacing
        grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")
        self.positions = np.stack([grid_x, grid_y], axis=-1)
        self.velocities = np.zeros_like(self.positions)

    def advance(self, dt: float) -> None:
        gravity = np.asarray(self.config.gravity, dtype=np.float64)
        self.positions += self.velocities * dt + 0.5 * gravity * (dt * dt)
        self.velocities += gravity * dt

    def sync(self) -> None:
        return None

    def positions_numpy(self) -> np.ndarray:
        return self.positions.copy()

    def run(self, frames: int, dt: float, *, collect_diagnostics: bool = False):
        for _ in range(int(frames)):
            self.advance(dt)
        seconds = 0.003 * float(frames) if self.config.prefer_gpu else 0.009 * float(frames)
        return SimpleNamespace(
            active_arch=self.active_arch,
            seconds=seconds,
            frames=frames,
        )


class _FakeTaichiApi:
    def __init__(self, *, available: bool = True, initialized: bool = True, gpu_available: bool = True) -> None:
        self.available = available
        self.initialized = initialized
        self.gpu_available = gpu_available
        self.reset_calls = 0
        self.TaichiXPBDClothSystem = _FakeClothSystem

    def reset_taichi_runtime(self) -> None:
        self.reset_calls += 1

    def get_taichi_xpbd_backend_status(self, prefer_gpu: bool = True):
        if not self.available:
            return SimpleNamespace(
                available=False,
                initialized=False,
                active_arch="unavailable",
                import_error="taichi missing",
            )
        if not self.initialized:
            return SimpleNamespace(
                available=True,
                initialized=False,
                active_arch="failed",
                import_error="runtime init failed",
            )
        if prefer_gpu and self.gpu_available:
            return SimpleNamespace(
                available=True,
                initialized=True,
                active_arch="cuda",
                import_error="",
            )
        return SimpleNamespace(
            available=True,
            initialized=True,
            active_arch="cpu",
            import_error="",
        )

    def create_default_taichi_cloth_config(self, particle_budget: int) -> _FakeConfig:
        side = max(int(round(max(particle_budget, 16) ** 0.5)), 4)
        return _FakeConfig(width=side, height=side)


def _read_report(manifest) -> dict:
    path = Path(manifest.outputs["report_file"])
    return json.loads(path.read_text(encoding="utf-8"))


def test_taichi_benchmark_backend_registered():
    reg = get_registry()
    meta, _cls = reg.get_or_raise(BackendType.TAICHI_XPBD)
    assert meta.name == BackendType.TAICHI_XPBD
    assert ArtifactFamily.BENCHMARK_REPORT.value in meta.artifact_families
    assert BackendCapability.GPU_ACCELERATED in meta.capabilities


def test_benchmark_report_family_required_metadata():
    required = ArtifactFamily.required_metadata_keys(ArtifactFamily.BENCHMARK_REPORT.value)
    assert "solver_type" in required
    assert "frame_count" in required
    assert "wall_time_ms" in required
    assert "particles_per_second" in required
    assert "gpu_device_name" in required
    assert "speedup_ratio" in required
    assert "cpu_gpu_max_drift" in required


def test_taichi_benchmark_backend_degrades_without_taichi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend = TaichiXPBDBackend()
    fake_tx = _FakeTaichiApi(available=False, initialized=False)
    monkeypatch.setattr(backend, "_load_taichi_api", lambda: fake_tx)

    ctx, _ = backend.validate_config({"output_dir": str(tmp_path), "name": "deg"})
    manifest = backend.execute(ctx)
    report = _read_report(manifest)

    assert validate_artifact(manifest) == []
    assert manifest.artifact_family == ArtifactFamily.BENCHMARK_REPORT.value
    assert report["degraded"] is True
    assert report["device"] == "unavailable"
    assert report["gpu_device_name"] == "unavailable"
    assert report["particles_per_second"] == 0.0
    assert report["speedup_ratio"] == 0.0
    assert report["cpu_gpu_max_drift"] == 0.0
    assert fake_tx.reset_calls == 1


def test_taichi_benchmark_backend_gpu_report_contains_median_sync_and_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend = TaichiXPBDBackend()
    fake_tx = _FakeTaichiApi(available=True, initialized=True, gpu_available=True)
    monkeypatch.setattr(backend, "_load_taichi_api", lambda: fake_tx)
    monkeypatch.setattr(backend, "_query_gpu_device_name", lambda actual_device: "Fake RTX 4070" if actual_device == "gpu" else "unavailable")
    gpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "gpu",
            "benchmark_device": "gpu",
            "benchmark_scenario": "free_fall_cloud",
            "benchmark_frame_count": 6,
            "benchmark_warmup_frames": 2,
            "benchmark_sample_count": 3,
            "particle_budget": 64,
        },
    )

    gpu_manifest = backend.execute(gpu_ctx)
    gpu_report = _read_report(gpu_manifest)

    assert validate_artifact(gpu_manifest) == []
    assert gpu_report["device"] == "gpu"
    assert gpu_report["sample_statistic"] == "median"
    assert gpu_report["explicit_sync_used"] is True
    assert gpu_report["gpu_device_name"] == "Fake RTX 4070"
    assert len(gpu_report["samples_ms"]) == 3
    assert len(gpu_report["cpu_reference_samples_ms"]) == 3
    assert gpu_report["speedup_ratio"] > 0.0
    assert gpu_report["cpu_gpu_max_drift"] == pytest.approx(0.0, abs=1e-12)
    assert gpu_report["cpu_gpu_rmse"] == pytest.approx(0.0, abs=1e-12)
    assert gpu_report["parity_passed"] is True


def test_runtime_distill_can_record_benchmark_report():
    bus = RuntimeDistillationBus(project_root="/tmp/runtime_distill_bench")
    normalized = bus.record_benchmark_report(
        {
            "metadata": {
                "solver_type": "taichi_xpbd",
                "device": "gpu",
                "frame_count": 8,
                "wall_time_ms": 12.5,
                "particles_per_second": 64000.0,
                "gpu_device_name": "Fake RTX 4070",
                "speedup_ratio": 3.25,
                "cpu_gpu_max_drift": 1.2e-6,
                "parity_passed": True,
            },
        },
    )
    assert normalized["solver_type"] == "taichi_xpbd"
    assert normalized["device"] == "gpu"
    assert normalized["throughput_per_s"] == 64000.0
    assert normalized["gpu_device_name"] == "Fake RTX 4070"
    assert normalized["speedup_ratio"] == pytest.approx(3.25)
    assert normalized["cpu_gpu_max_drift"] == pytest.approx(1.2e-6)
    assert normalized["parity_passed"] is True
    assert bus.last_refresh_summary["benchmark_reports"][-1]["frame_count"] == 8


def test_runtime_rule_program_benchmark_emits_device_and_throughput():
    bus = RuntimeDistillationBus(project_root=Path(__file__).resolve().parent.parent)
    summary = bus.refresh_from_knowledge()
    if not bus.runtime_programs:
        pytest.skip("runtime knowledge unavailable for benchmark smoke")
    program = next(iter(bus.runtime_programs.values()))
    bench = program.benchmark(sample_count=16, device="cpu")
    assert bench["device"] == "cpu"
    assert bench["throughput_per_s"] > 0.0
    assert bench["wall_time_ms"] >= 0.0
    assert summary["module_count"] >= 1


def test_distillation_record_declares_benchmark_fields():
    text = _EVOLUTION_LOOP_PATH.read_text(encoding="utf-8")
    assert "benchmark_solver_type: Optional[str] = None" in text
    assert "benchmark_device: Optional[str] = None" in text
    assert "benchmark_wall_time_ms: Optional[float] = None" in text
    assert "benchmark_throughput_per_s: Optional[float] = None" in text
    assert "return asdict(self)" in text


def test_real_taichi_backend_cpu_smoke_and_optional_gpu(tmp_path: Path):
    backend = TaichiXPBDBackend()
    cpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "real_cpu",
            "benchmark_device": "cpu",
            "benchmark_scenario": "free_fall_cloud",
            "benchmark_frame_count": 2,
            "benchmark_warmup_frames": 1,
            "benchmark_sample_count": 2,
            "particle_budget": 16,
        },
    )
    cpu_manifest = backend.execute(cpu_ctx)
    assert validate_artifact(cpu_manifest) == []
    cpu_report = _read_report(cpu_manifest)
    assert cpu_report["sample_statistic"] == "median"
    assert cpu_report["explicit_sync_used"] is True
    assert "gpu_device_name" in cpu_report
    assert "cpu_gpu_max_drift" in cpu_report

    if cpu_report["degraded"]:
        return

    gpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "real_gpu",
            "benchmark_device": "gpu",
            "benchmark_scenario": "free_fall_cloud",
            "benchmark_frame_count": 2,
            "benchmark_warmup_frames": 1,
            "benchmark_sample_count": 2,
            "particle_budget": 16,
        },
    )
    gpu_manifest = backend.execute(gpu_ctx)
    assert validate_artifact(gpu_manifest) == []
    gpu_report = _read_report(gpu_manifest)
    if gpu_report["degraded"] or gpu_report["device"] != "gpu":
        pytest.skip("No real GPU Taichi lane available in this environment")
    assert gpu_report["particles_per_second"] > 0.0
    assert gpu_report["explicit_sync_used"] is True
    assert np.isfinite(gpu_report["cpu_gpu_max_drift"])
    assert np.isfinite(gpu_report["speedup_ratio"])
