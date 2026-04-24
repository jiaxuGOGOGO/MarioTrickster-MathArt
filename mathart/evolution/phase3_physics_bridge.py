"""SESSION-058: Phase 3 Evolution Bridge — Taichi XPBD + SDF CCD + NSM Gait.

This bridge turns the newly added Phase 3 systems into a repository-native
three-layer loop:

1. Layer 1 — Internal Evolution:
   Evaluate whether the Taichi cloth backend, SDF CCD, and distilled NSM gait
   runtime all produce valid, finite, and asymmetry-aware outputs.
2. Layer 2 — External Knowledge Distillation:
   Persist research-derived engineering rules so later sessions can continue
   tuning and extending the same stack.
3. Layer 3 — Self-Iterating Test:
   Update persistent trends and produce a bounded fitness bonus that can be
   consumed by higher-level orchestration.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..animation.biomechanics import FABRIKGaitGenerator
from ..animation.nsm_gait import (
    DistilledNeuralStateMachine,
    BIPED_LIMP_RIGHT_PROFILE,
    QUADRUPED_TROT_PROFILE,
    generate_asymmetric_biped_pose,
    plan_quadruped_gait,
)
from ..animation.sdf_ccd import SDFSphereTracingCCD
from ..animation.terrain_sensor import create_flat_terrain
from ..animation.xpbd_solver import XPBDSolver
from ..animation.xpbd_taichi import (
    TaichiXPBDClothSystem,
    create_default_taichi_cloth_config,
    get_taichi_xpbd_backend_status,
)
from ..animation.skeleton import Skeleton
from .state_vault import resolve_state_path


@dataclass
class Phase3PhysicsMetrics:
    cycle_id: int = 0
    timestamp: str = ""
    taichi_backend_available: bool = False
    taichi_cloth_particles: int = 0
    taichi_cloth_finite: bool = False
    ccd_hit: bool = False
    ccd_toi: float = 1.0
    ccd_safe_height: float = 0.0
    nsm_biped_asymmetry: float = 0.0
    nsm_quadruped_diagonal_error: float = 1.0
    all_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Phase3PhysicsState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_biped_asymmetry: float = 0.0
    best_ccd_toi: float = 1.0
    knowledge_rules_total: int = 0
    asymmetry_trend: list[float] = field(default_factory=list)
    diagonal_error_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_biped_asymmetry": self.best_biped_asymmetry,
            "best_ccd_toi": self.best_ccd_toi,
            "knowledge_rules_total": self.knowledge_rules_total,
            "asymmetry_trend": self.asymmetry_trend[-50:],
            "diagonal_error_trend": self.diagonal_error_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Phase3PhysicsState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            consecutive_passes=int(data.get("consecutive_passes", 0)),
            best_biped_asymmetry=float(data.get("best_biped_asymmetry", 0.0)),
            best_ccd_toi=float(data.get("best_ccd_toi", 1.0)),
            knowledge_rules_total=int(data.get("knowledge_rules_total", 0)),
            asymmetry_trend=list(data.get("asymmetry_trend", [])),
            diagonal_error_trend=list(data.get("diagonal_error_trend", [])),
            history=list(data.get("history", [])),
        )


@dataclass
class Phase3PhysicsStatus:
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
        "id": "P3-RULE-001",
        "source": "Hu 2019 / Taichi",
        "rule": "Express XPBD cloth kernels in Python semantics and let Taichi JIT lower them to GPU-oriented kernels; do not hand-write CUDA for this repository.",
        "constraint": "cloth_backend = taichi_jit_python_kernel",
    },
    {
        "id": "P3-RULE-002",
        "source": "Taichi cloth + sparse docs",
        "rule": "Large cloth workloads should scale through grid/mesh particle layouts and sparse-friendly storage rather than bespoke loop nests hidden in scripts.",
        "constraint": "cloth_particles >= 50000 when backend_available",
    },
    {
        "id": "P3-RULE-003",
        "source": "Erwin Coumans / Bullet CCD",
        "rule": "High-speed weapons or particles must use continuous collision detection by tracing motion over the full frame and clamping before penetration, not merely resolving overlap after tunneling.",
        "constraint": "toi < 1.0 => clamp_before_penetration",
    },
    {
        "id": "P3-RULE-004",
        "source": "Sebastian Starke / NSM + Local Motion Phases + DeepPhase",
        "rule": "Asymmetric or creature locomotion should be represented with per-limb local phases and contact labels rather than a single symmetric global phase variable.",
        "constraint": "gait_state = multi_contact_local_phases",
    },
    {
        "id": "P3-RULE-005",
        "source": "Project integration policy",
        "rule": "FABRIK is the existing IK substrate for locomotion in this repository, so new gait intelligence must emit compatible target offsets and contact metadata instead of bypassing the current stack.",
        "constraint": "nsm_output -> fabrik_targets + contact_labels",
    },
]


def collect_phase3_physics_status(project_root: str | Path) -> Phase3PhysicsStatus:
    root = Path(project_root)
    module_path = root / "mathart/animation/nsm_gait.py"
    bridge_path = root / "mathart/evolution/phase3_physics_bridge.py"
    animation_api = root / "mathart/animation/__init__.py"
    evolution_api = root / "mathart/evolution/__init__.py"
    test_paths = [
        root / "tests/test_taichi_xpbd.py",
        root / "tests/test_sdf_ccd.py",
        root / "tests/test_nsm_gait.py",
    ]
    knowledge_path = root / "knowledge/phase3_physics_rules.md"
    state_path = resolve_state_path(root, ".phase3_physics_state.json")

    tracked_exports: list[str] = []
    if module_path.exists():
        text = module_path.read_text(encoding="utf-8", errors="replace")
        for name in (
            "DistilledNeuralStateMachine",
            "generate_asymmetric_biped_pose",
            "plan_quadruped_gait",
            "BIPED_LIMP_RIGHT_PROFILE",
            "QUADRUPED_TROT_PROFILE",
        ):
            if name in text:
                tracked_exports.append(name)

    animation_api_exports = False
    if animation_api.exists():
        text = animation_api.read_text(encoding="utf-8", errors="replace")
        animation_api_exports = (
            "TaichiXPBDClothSystem" in text
            and "SDFSphereTracingCCD" in text
            and "DistilledNeuralStateMachine" in text
        )

    evolution_api_exports = False
    if evolution_api.exists():
        text = evolution_api.read_text(encoding="utf-8", errors="replace")
        evolution_api_exports = "Phase3PhysicsEvolutionBridge" in text

    total_cycles = 0
    consecutive_passes = 0
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = int(data.get("total_cycles", 0))
            consecutive_passes = int(data.get("consecutive_passes", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    return Phase3PhysicsStatus(
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


class Phase3PhysicsEvolutionBridge:
    """Three-layer bridge for SESSION-058 Phase 3 systems."""

    STATE_FILE = "phase3_physics_state.json"
    KNOWLEDGE_FILE = "knowledge/phase3_physics_rules.md"

    def __init__(self, project_root: str | Path, verbose: bool = True) -> None:
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state_path = resolve_state_path(self.project_root, self.STATE_FILE)
        self.knowledge_path = self.project_root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[phase3-bridge] {msg}")

    def _load_state(self) -> Phase3PhysicsState:
        if not self.state_path.exists():
            return Phase3PhysicsState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return Phase3PhysicsState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return Phase3PhysicsState()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate_phase3_stack(self) -> Phase3PhysicsMetrics:
        metrics = Phase3PhysicsMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Layer 1a — Taichi cloth backend smoke evaluation
        backend = get_taichi_xpbd_backend_status()
        metrics.taichi_backend_available = bool(getattr(backend, "available", False))
        if metrics.taichi_backend_available:
            cfg = create_default_taichi_cloth_config(particle_budget=256)
            cloth = TaichiXPBDClothSystem(cfg)
            diag = cloth.step(1.0 / 120.0)
            positions = np.asarray(cloth.positions_numpy(), dtype=np.float64)
            metrics.taichi_cloth_particles = int(positions.shape[0])
            metrics.taichi_cloth_finite = bool(np.isfinite(positions).all() and math.isfinite(float(diag.max_velocity_observed)))

        # Layer 1b — SDF CCD hit / TOI evaluation
        terrain = create_flat_terrain(ground_y=0.0)
        detector = SDFSphereTracingCCD(terrain)
        ccd_result = detector.trace_motion((0.0, 1.0), (0.0, -1.0), radius=0.1)
        metrics.ccd_hit = bool(ccd_result.hit)
        metrics.ccd_toi = float(ccd_result.toi)
        metrics.ccd_safe_height = float(ccd_result.safe_point[1])

        # Layer 1c — NSM asymmetric biped + quadruped evaluation
        controller = DistilledNeuralStateMachine()
        biped_frame = controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=0.25, speed=1.0)
        left = biped_frame.contact_labels["l_foot"]
        right = biped_frame.contact_labels["r_foot"]
        metrics.nsm_biped_asymmetry = float(abs(left - right))

        quad_frame = plan_quadruped_gait(QUADRUPED_TROT_PROFILE, phase=0.10, speed=1.1)
        fl = quad_frame.contact_labels["front_left"]
        hr = quad_frame.contact_labels["hind_right"]
        fr = quad_frame.contact_labels["front_right"]
        hl = quad_frame.contact_labels["hind_left"]
        metrics.nsm_quadruped_diagonal_error = float(max(abs(fl - hr), abs(fr - hl)))

        # Layer 1d — ensure FABRIK integration path remains runnable
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        gait = FABRIKGaitGenerator(skeleton=skeleton)
        pose, _frame = generate_asymmetric_biped_pose(
            gait,
            phase=0.35,
            profile=BIPED_LIMP_RIGHT_PROFILE,
            speed=1.0,
        )
        pose_finite = all(math.isfinite(float(v)) for v in pose.values())

        metrics.all_pass = bool(
            metrics.taichi_backend_available
            and metrics.taichi_cloth_finite
            and metrics.ccd_hit
            and 0.0 < metrics.ccd_toi < 1.0
            and metrics.ccd_safe_height >= 0.099
            and metrics.nsm_biped_asymmetry >= 0.02
            and metrics.nsm_quadruped_diagonal_error <= 1e-6
            and pose_finite
        )
        return metrics

    def distill_rules(self) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# SESSION-058 Phase 3 Physics Rules",
            "",
            "This file is generated by `Phase3PhysicsEvolutionBridge` and stores distilled engineering rules for the Taichi XPBD / SDF CCD / NSM gait stack.",
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

    def update_state_and_compute_bonus(self, metrics: Phase3PhysicsMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.all_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_biped_asymmetry = max(self.state.best_biped_asymmetry, metrics.nsm_biped_asymmetry)
        if 0.0 < metrics.ccd_toi < 1.0:
            self.state.best_ccd_toi = min(self.state.best_ccd_toi, metrics.ccd_toi)
        self.state.asymmetry_trend.append(metrics.nsm_biped_asymmetry)
        self.state.diagonal_error_trend.append(metrics.nsm_quadruped_diagonal_error)
        self.state.history.append(metrics.to_dict())
        self._save_state()

        bonus = 0.0
        bonus += min(metrics.nsm_biped_asymmetry / 0.20, 1.0) * 0.10
        bonus += (0.10 if metrics.ccd_hit else 0.0)
        bonus += (0.10 if metrics.taichi_cloth_finite else 0.0)
        bonus += max(0.0, 0.10 - min(metrics.nsm_quadruped_diagonal_error, 0.10))
        return float(min(bonus, 0.40))

    def run_full_cycle(self) -> tuple[Phase3PhysicsMetrics, Path, float]:
        metrics = self.evaluate_phase3_stack()
        knowledge_path = self.distill_rules()
        bonus = self.update_state_and_compute_bonus(metrics)
        self._log(
            f"cycle={metrics.cycle_id} pass={metrics.all_pass} "
            f"asym={metrics.nsm_biped_asymmetry:.4f} toi={metrics.ccd_toi:.4f} bonus={bonus:.4f}"
        )
        return metrics, knowledge_path, bonus

    def status_report(self) -> str:
        status = collect_phase3_physics_status(self.project_root)
        return (
            "SESSION-058 Phase 3 Status\n"
            f"  module: {'yes' if status.module_exists else 'no'}\n"
            f"  bridge: {'yes' if status.bridge_exists else 'no'}\n"
            f"  animation api: {'yes' if status.animation_api_exports else 'no'}\n"
            f"  evolution api: {'yes' if status.evolution_api_exports else 'no'}\n"
            f"  tests: {'yes' if status.tests_exist else 'no'}\n"
            f"  total cycles: {status.total_cycles}\n"
            f"  consecutive passes: {status.consecutive_passes}"
        )


__all__ = [
    "Phase3PhysicsMetrics",
    "Phase3PhysicsState",
    "Phase3PhysicsStatus",
    "collect_phase3_physics_status",
    "Phase3PhysicsEvolutionBridge",
]
