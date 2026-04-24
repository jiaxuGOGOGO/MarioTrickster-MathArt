"""SESSION-059: Unity URP 2D native bridge.

This bridge closes the orchestration gap between MarioTrickster-MathArt's
Python-side multi-channel asset generation and Unity URP 2D's native runtime
consumption path.

Three-layer loop:

1. **Internal evolution**
   Validate that Unity-native helpers for Secondary Textures and VAT playback can
   be generated and that an offline XPBD cloth cache can be baked successfully.
2. **External knowledge distillation**
   Persist research-derived engineering rules for Dead Cells-style multi-channel
   sprites and XPBD -> VAT cloth export.
3. **Self-iterating test**
   Update state trends and expose a bounded fitness bonus so the unified
   orchestrator can continuously re-evaluate this stack.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state_vault import resolve_state_path
from ..animation.unity_urp_native import (
    UnityURP2DNativePipelineGenerator,
    XPBDVATBakeConfig,
    bake_cloth_vat,
)


@dataclass
class UnityURP2DMetrics:
    cycle_id: int = 0
    timestamp: str = ""
    importer_generated: bool = False
    secondary_texture_postprocessor_generated: bool = False
    vat_player_generated: bool = False
    vat_shader_generated: bool = False
    vat_manifest_valid: bool = False
    vat_frame_count: int = 0
    vat_vertex_count: int = 0
    taichi_backend_used: bool = False
    all_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnityURP2DState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_vat_frame_count: int = 0
    best_vat_vertex_count: int = 0
    knowledge_rules_total: int = 0
    frame_count_trend: list[int] = field(default_factory=list)
    vertex_count_trend: list[int] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_vat_frame_count": self.best_vat_frame_count,
            "best_vat_vertex_count": self.best_vat_vertex_count,
            "knowledge_rules_total": self.knowledge_rules_total,
            "frame_count_trend": self.frame_count_trend[-50:],
            "vertex_count_trend": self.vertex_count_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnityURP2DState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            consecutive_passes=int(data.get("consecutive_passes", 0)),
            best_vat_frame_count=int(data.get("best_vat_frame_count", 0)),
            best_vat_vertex_count=int(data.get("best_vat_vertex_count", 0)),
            knowledge_rules_total=int(data.get("knowledge_rules_total", 0)),
            frame_count_trend=list(data.get("frame_count_trend", [])),
            vertex_count_trend=list(data.get("vertex_count_trend", [])),
            history=list(data.get("history", [])),
        )


@dataclass
class UnityURP2DStatus:
    module_exists: bool = False
    bridge_exists: bool = False
    animation_api_exports: bool = False
    evolution_api_exports: bool = False
    tests_exist: bool = False
    knowledge_path: str = ""
    state_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    total_cycles: int = 0
    consecutive_passes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DISTILLED_KNOWLEDGE: list[dict[str, str]] = [
    {
        "id": "UNITY-URP2D-001",
        "source": "Unity URP 2D Secondary Textures manual",
        "rule": "Treat `_NormalMap` as a first-class secondary texture on sprite imports so Light2D can read repository-baked normals without manual artist wiring.",
        "constraint": "normal.png -> SecondaryTexture['_NormalMap']",
    },
    {
        "id": "UNITY-URP2D-002",
        "source": "Unity Sprite Editor data provider API",
        "rule": "Use editor-side Sprite data providers and `ISecondaryTextureDataProvider` for deterministic import automation instead of relying on manual inspector edits.",
        "constraint": "secondary textures must be written by editor automation",
    },
    {
        "id": "UNITY-URP2D-003",
        "source": "Dead Cells pipeline references",
        "rule": "Export multi-channel 2D assets as a coherent bundle: albedo plus lighting helpers such as normal/depth/mask should survive together from the Python pipeline into the engine.",
        "constraint": "bundle = albedo + auxiliary maps + metadata",
    },
    {
        "id": "UNITY-URP2D-004",
        "source": "Macklin et al. 2016 XPBD",
        "rule": "Run cloth simulation offline where compliance-stable XPBD can iterate freely, then cache the result for downstream engines instead of forcing Unity 2D to solve the cloth at runtime.",
        "constraint": "cloth_runtime = offline_cache_replay",
    },
    {
        "id": "UNITY-URP2D-005",
        "source": "VAT in Unity / SideFX workflow",
        "rule": "Store one vertex per texel and one frame per scanline so mesh deformation can be replayed entirely in shader code with near-zero CPU animation overhead.",
        "constraint": "VAT width = vertex count; VAT height = frame count",
    },
]


def collect_unity_urp_2d_status(project_root: str | Path) -> UnityURP2DStatus:
    root = Path(project_root)
    module_path = root / "mathart/animation/unity_urp_native.py"
    bridge_path = root / "mathart/evolution/unity_urp_2d_bridge.py"
    animation_api = root / "mathart/animation/__init__.py"
    evolution_api = root / "mathart/evolution/__init__.py"
    test_paths = [
        root / "tests/test_unity_urp_native.py",
    ]
    knowledge_path = root / "knowledge/unity_urp_2d_rules.md"
    state_path = resolve_state_path(root, ".unity_urp_2d_state.json")

    tracked_exports: list[str] = []
    if module_path.exists():
        text = module_path.read_text(encoding="utf-8", errors="replace")
        for name in (
            "UnityURP2DNativePipelineGenerator",
            "XPBDVATBakeConfig",
            "VATBakeManifest",
            "bake_cloth_vat",
        ):
            if name in text:
                tracked_exports.append(name)

    animation_api_exports = False
    if animation_api.exists():
        text = animation_api.read_text(encoding="utf-8", errors="replace")
        animation_api_exports = (
            "UnityURP2DNativePipelineGenerator" in text
            and "XPBDVATBakeConfig" in text
            and "bake_cloth_vat" in text
        )

    evolution_api_exports = False
    if evolution_api.exists():
        text = evolution_api.read_text(encoding="utf-8", errors="replace")
        evolution_api_exports = "UnityURP2DEvolutionBridge" in text

    total_cycles = 0
    consecutive_passes = 0
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = int(data.get("total_cycles", 0))
            consecutive_passes = int(data.get("consecutive_passes", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    return UnityURP2DStatus(
        module_exists=module_path.exists(),
        bridge_exists=bridge_path.exists(),
        animation_api_exports=animation_api_exports,
        evolution_api_exports=evolution_api_exports,
        tests_exist=all(p.exists() for p in test_paths),
        knowledge_path=str(knowledge_path.relative_to(root)) if knowledge_path.exists() else "",
        state_path=str(state_path.relative_to(root)) if state_path.exists() else "",
        tracked_exports=tracked_exports,
        total_cycles=total_cycles,
        consecutive_passes=consecutive_passes,
    )


class UnityURP2DEvolutionBridge:
    """Three-layer bridge for Unity URP 2D native sprite/VAT integration."""

    STATE_FILE = "unity_urp_2d_state.json"
    KNOWLEDGE_FILE = "knowledge/unity_urp_2d_rules.md"

    def __init__(self, project_root: str | Path, verbose: bool = True) -> None:
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state_path = resolve_state_path(self.project_root, self.STATE_FILE)
        self.knowledge_path = self.project_root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[unity-urp2d-bridge] {msg}")

    def _load_state(self) -> UnityURP2DState:
        if not self.state_path.exists():
            return UnityURP2DState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return UnityURP2DState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return UnityURP2DState()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate_native_stack(self) -> UnityURP2DMetrics:
        metrics = UnityURP2DMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        with tempfile.TemporaryDirectory(prefix="unity_urp2d_", dir=str(self.project_root)) as tmpdir:
            tmp = Path(tmpdir)
            pipeline_dir = tmp / "unity_native_pipeline"
            generator = UnityURP2DNativePipelineGenerator()
            generator.generate(pipeline_dir)
            audit = generator.audit(pipeline_dir)

            vat_dir = tmp / "vat"
            bake = bake_cloth_vat(
                vat_dir,
                config=XPBDVATBakeConfig(
                    asset_name="session059_cloth_probe",
                    frame_count=12,
                    fps=24,
                    particle_budget=144,
                    include_preview=True,
                    allow_synthetic_fallback=True,
                ),
            )

            manifest = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
            metrics.importer_generated = audit.importer_exists
            metrics.secondary_texture_postprocessor_generated = audit.postprocessor_exists
            metrics.vat_player_generated = audit.vat_player_exists
            metrics.vat_shader_generated = audit.vat_shader_exists
            metrics.vat_manifest_valid = (
                manifest.get("frame_count", 0) > 0
                and manifest.get("vertex_count", 0) > 0
                and manifest.get("texture_width", 0) == manifest.get("vertex_count", 0)
                and manifest.get("texture_height", 0) == manifest.get("frame_count", 0)
            )
            metrics.vat_frame_count = int(manifest.get("frame_count", 0))
            metrics.vat_vertex_count = int(manifest.get("vertex_count", 0))
            metrics.taichi_backend_used = bool(bake.diagnostics.get("backend_available", False))
            metrics.all_pass = bool(
                audit.all_pass
                and metrics.vat_manifest_valid
                and metrics.vat_frame_count >= 8
                and metrics.vat_vertex_count >= 16
            )

        return metrics

    def distill_rules(self) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# SESSION-059 Unity URP 2D Native Rules",
            "",
            "This file is generated by `UnityURP2DEvolutionBridge` and stores distilled engineering rules for Unity URP 2D secondary textures plus XPBD-to-VAT export.",
            "",
        ]
        for item in DISTILLED_KNOWLEDGE:
            lines.extend([
                f"## {item['id']} — {item['source']}",
                "",
                item["rule"],
                "",
                f"- Constraint: `{item['constraint']}`",
                "",
            ])
        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")
        self.state.knowledge_rules_total = len(DISTILLED_KNOWLEDGE)
        return self.knowledge_path

    def update_state_and_compute_bonus(self, metrics: UnityURP2DMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.all_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_vat_frame_count = max(self.state.best_vat_frame_count, metrics.vat_frame_count)
        self.state.best_vat_vertex_count = max(self.state.best_vat_vertex_count, metrics.vat_vertex_count)
        self.state.frame_count_trend.append(metrics.vat_frame_count)
        self.state.vertex_count_trend.append(metrics.vat_vertex_count)
        self.state.history.append(metrics.to_dict())
        self._save_state()

        bonus = 0.0
        bonus += 0.10 if metrics.secondary_texture_postprocessor_generated else 0.0
        bonus += 0.10 if metrics.vat_shader_generated and metrics.vat_player_generated else 0.0
        bonus += min(metrics.vat_frame_count / 24.0, 1.0) * 0.10
        bonus += min(metrics.vat_vertex_count / 128.0, 1.0) * 0.10
        return float(min(bonus, 0.40))

    def run_full_cycle(self) -> tuple[UnityURP2DMetrics, Path, float]:
        metrics = self.evaluate_native_stack()
        knowledge_path = self.distill_rules()
        bonus = self.update_state_and_compute_bonus(metrics)
        self._log(
            f"cycle={metrics.cycle_id} pass={metrics.all_pass} "
            f"frames={metrics.vat_frame_count} vertices={metrics.vat_vertex_count} bonus={bonus:.4f}"
        )
        return metrics, knowledge_path, bonus

    def status_report(self) -> str:
        status = collect_unity_urp_2d_status(self.project_root)
        return (
            "SESSION-059 Unity URP 2D Status\n"
            f"  module: {'yes' if status.module_exists else 'no'}\n"
            f"  bridge: {'yes' if status.bridge_exists else 'no'}\n"
            f"  animation api: {'yes' if status.animation_api_exports else 'no'}\n"
            f"  evolution api: {'yes' if status.evolution_api_exports else 'no'}\n"
            f"  tests: {'yes' if status.tests_exist else 'no'}\n"
            f"  total cycles: {status.total_cycles}\n"
            f"  consecutive passes: {status.consecutive_passes}"
        )


__all__ = [
    "UnityURP2DMetrics",
    "UnityURP2DState",
    "UnityURP2DStatus",
    "collect_unity_urp_2d_status",
    "UnityURP2DEvolutionBridge",
]
