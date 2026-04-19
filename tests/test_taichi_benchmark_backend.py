"""Tests for SESSION-075 (P1-DISTILL-1B) — Taichi benchmark backend.

The suite is intentionally split into three layers:

1. Pure schema / registry guards that must pass everywhere.
2. Optional-dependency isolation via fake Taichi modules, ensuring CI can
   validate the backend even when ``taichi`` is unavailable.
3. A small real-runtime smoke path that only skips the GPU-specific assertion,
   not the entire file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import json
import sys

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
    prefer_gpu: bool = True
    sub_steps: int = 4
    solver_iterations: int = 8

    @property
    def particle_count(self) -> int:
        return int(self.width * self.height)

    @property
    def total_constraint_count(self) -> int:
        return int((self.width - 1) * self.height + self.width * (self.height - 1))


class _FakeClothSystem:
    def __init__(self, config: _FakeConfig) -> None:
        self.config = config
        self.active_arch = "cuda" if config.prefer_gpu else "cpu"
        self.advanced = 0

    def advance(self, dt: float) -> None:
        self.advanced += 1

    def sync(self) -> None:
        return None

    def run(self, frames: int, dt: float, *, collect_diagnostics: bool = False):
        seconds = 0.006 if self.config.prefer_gpu else 0.018
        return SimpleNamespace(
            active_arch=self.active_arch,
            seconds=seconds,
            frames=frames,
        )


class _FakeTaichiApi:
    def __init__(self, *, available: bool = True, initialized: bool = True) -> None:
        self._status = SimpleNamespace(
            available=available,
            initialized=initialized,
            active_arch="cpu" if initialized else "unavailable",
            import_error="taichi missing" if not initialized else "",
        )
        self.reset_calls = 0
        self.TaichiXPBDClothSystem = _FakeClothSystem

    def reset_taichi_runtime(self) -> None:
        self.reset_calls += 1

    def get_taichi_xpbd_backend_status(self, prefer_gpu: bool = True):
        return self._status

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
    assert report["particles_per_second"] == 0.0
    assert fake_tx.reset_calls == 1


def test_taichi_benchmark_backend_cpu_gpu_ab_compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend = TaichiXPBDBackend()
    fake_tx = _FakeTaichiApi(available=True, initialized=True)
    monkeypatch.setattr(backend, "_load_taichi_api", lambda: fake_tx)

    cpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "cpu",
            "benchmark_device": "cpu",
            "benchmark_frame_count": 6,
            "benchmark_sample_count": 3,
            "particle_budget": 64,
        },
    )
    gpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "gpu",
            "benchmark_device": "gpu",
            "benchmark_frame_count": 6,
            "benchmark_sample_count": 3,
            "particle_budget": 64,
        },
    )

    cpu_manifest = backend.execute(cpu_ctx)
    gpu_manifest = backend.execute(gpu_ctx)
    cpu_report = _read_report(cpu_manifest)
    gpu_report = _read_report(gpu_manifest)

    assert validate_artifact(cpu_manifest) == []
    assert validate_artifact(gpu_manifest) == []
    assert cpu_report["device"] == "cpu"
    assert gpu_report["device"] == "gpu"
    assert gpu_report["particles_per_second"] > cpu_report["particles_per_second"]
    assert gpu_report["wall_time_ms"] < cpu_report["wall_time_ms"]


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
            },
        },
    )
    assert normalized["solver_type"] == "taichi_xpbd"
    assert normalized["device"] == "gpu"
    assert normalized["throughput_per_s"] == 64000.0
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
            "benchmark_frame_count": 2,
            "benchmark_warmup_frames": 1,
            "benchmark_sample_count": 2,
            "particle_budget": 16,
        },
    )
    cpu_manifest = backend.execute(cpu_ctx)
    assert validate_artifact(cpu_manifest) == []
    cpu_report = _read_report(cpu_manifest)
    assert "device" in cpu_report
    assert "degraded" in cpu_report

    if cpu_report["degraded"]:
        return

    gpu_ctx, _ = backend.validate_config(
        {
            "output_dir": str(tmp_path),
            "name": "real_gpu",
            "benchmark_device": "gpu",
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
